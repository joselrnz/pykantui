"""Record the README demo as an animated gif.

    python tools/gif.py                 # assets/demo.gif
    python tools/gif.py --out /tmp/x.gif --scale 1

No terminal recorder involved. The app runs under Textual's pilot, and after
each scripted key press the screen is read straight off the compositor as
styled cells, drawn to a PNG with Pillow, and the frames are stitched by
ffmpeg. That is a lot less machinery than it sounds like, and unlike vhs or
asciinema it needs no browser, no pty and no terminal emulator — which is why
it works the same on a developer's laptop and in CI.

Requires Pillow and ffmpeg on PATH.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.segment import Segment
from textual.pilot import Pilot

from pykantui.sync.jsonstore import demo_backend
from pykantui.tui.app import KanbanApp

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

#: Wide enough for five columns at their 20-cell floor, plus the borders.
COLUMNS, ROWS = 112, 32

#: Consolas first: it ships with Windows and covers the box-drawing and arrow
#: glyphs the board leans on. The rest are the usual Linux and macOS monospaces.
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\CascadiaMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

FONT_SIZE = 17

#: Fallback colours, for the rare cell that carries no style of its own.
DEFAULT_BG = (18, 18, 18)
DEFAULT_FG = (204, 204, 204)


@dataclass
class Beat:
    """One step of the demo: press these keys, then hold the result."""

    keys: tuple[str, ...] = ()
    hold: float = 0.8
    """Seconds the finished frame stays on screen."""

    settle: int = 3
    """Pauses to let Textual finish reacting before the screen is read.

    Deliberately not ``workers.wait_for_complete()``: the move confirmation is
    a worker that sits awaiting a modal, so draining workers would deadlock on
    the very frame the demo exists to show.
    """


#: The demo itself. Lowercase moves the cursor, uppercase moves the card — the
#: distinction the board is built around, so it goes first.
SCRIPT: list[Beat] = [
    Beat(hold=1.4),
    Beat(("l",), hold=0.55),
    Beat(("l",), hold=0.55),
    Beat(("h",), hold=0.7),
    # A column move asks first, naming both columns and any side effect.
    Beat(("L",), hold=1.9),
    Beat(("enter",), hold=1.3),
    # The card popup: dates, dependencies, description, Jira fields.
    Beat(("v",), hold=2.3),
    Beat(("escape",), hold=0.7),
    # Collapse a column to a strip, then expand everything.
    Beat(("z",), hold=1.6),
    Beat(("Z",), hold=1.1),
    # Search filters live; the count reads "n of m" while it does.
    Beat(("slash",), hold=0.7),
    Beat(("s", "h", "i", "p"), hold=2.4),
    Beat(("backspace",) * 4, hold=1.2),
    Beat(("escape",), hold=1.0),
]


@dataclass
class Grid:
    """One screen, as rows of (character, foreground, background)."""

    cells: list[list[tuple[str, tuple[int, int, int], tuple[int, int, int]]]] = field(default_factory=list)

    def key(self) -> tuple[object, ...]:
        """An identity, so an unchanged screen is not drawn twice.

        Colours are part of it, not just the characters. Moving the cursor
        between cards changes nothing *but* colour — key on the text alone and
        every focus move dedupes away, which is most of what the demo is
        showing.
        """
        return tuple(tuple(row) for row in self.cells)


def _triplet(color: object, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if color is None:
        return fallback
    triplet = color.get_truecolor()  # type: ignore[attr-defined]
    return (triplet.red, triplet.green, triplet.blue)


def capture(app: KanbanApp) -> Grid:
    """Read the rendered screen off the compositor as styled cells."""
    grid = Grid()
    for strip in app.screen._compositor.render_strips():
        row: list[tuple[str, tuple[int, int, int], tuple[int, int, int]]] = []
        segment: Segment
        for segment in strip:
            style = segment.style
            foreground = _triplet(style.color if style else None, DEFAULT_FG)
            background = _triplet(style.bgcolor if style else None, DEFAULT_BG)
            for character in segment.text:
                row.append((character, foreground, background))
        row = (row + [(" ", DEFAULT_FG, DEFAULT_BG)] * COLUMNS)[:COLUMNS]
        grid.cells.append(row)
    return grid


def load_font() -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, FONT_SIZE)
    raise SystemExit("no monospace font found; tried:\n  " + "\n  ".join(FONT_CANDIDATES))


def draw(grid: Grid, font: ImageFont.FreeTypeFont, cell: tuple[int, int]) -> Image.Image:
    """Render one screen.

    Backgrounds are filled per run of identical colour, then glyphs are drawn
    one cell at a time. Per cell rather than per run because a fractional
    advance width drifts across a long run, and drift is exactly what breaks
    the board's box-drawing into a dashed mess.
    """
    cell_width, cell_height = cell
    image = Image.new("RGB", (COLUMNS * cell_width, len(grid.cells) * cell_height), DEFAULT_BG)
    canvas = ImageDraw.Draw(image)

    for y, row in enumerate(grid.cells):
        top = y * cell_height
        run_start = 0
        # None is the end-of-row sentinel: it can never equal a real colour, so
        # the final run is always flushed. It is never used as a fill.
        run_colour: tuple[int, int, int] | None = row[0][2] if row else DEFAULT_BG
        sentinel: tuple[str, tuple[int, int, int], tuple[int, int, int] | None] = ("", DEFAULT_FG, None)
        for x, (_, _, background) in enumerate([*row, sentinel]):
            if background != run_colour:
                canvas.rectangle(
                    [run_start * cell_width, top, x * cell_width - 1, top + cell_height - 1],
                    fill=run_colour,
                )
                run_start, run_colour = x, background

        for x, (character, foreground, _) in enumerate(row):
            if character and not character.isspace():
                canvas.text((x * cell_width, top), character, font=font, fill=foreground)

    return image


async def record(directory: Path, font: ImageFont.FreeTypeFont, cell: tuple[int, int]) -> list[tuple[Path, float]]:
    """Run the script, writing one PNG per distinct screen."""
    app = KanbanApp(backend=demo_backend(), confirm_moves=True)
    timeline: list[tuple[Path, float]] = []
    seen: dict[tuple[object, ...], Path] = {}

    async with app.run_test(size=(COLUMNS, ROWS)) as pilot:
        pilot_typed: Pilot[None] = pilot
        await pilot_typed.pause()

        for index, beat in enumerate(SCRIPT):
            for key in beat.keys:
                await pilot_typed.press(key)
            for _ in range(beat.settle):
                await pilot_typed.pause()

            grid = capture(app)
            frame_key = grid.key()
            path = seen.get(frame_key)
            if path is None:
                path = directory / f"frame_{index:03d}.png"
                draw(grid, font, cell).save(path)
                seen[frame_key] = path
            timeline.append((path, beat.hold))
            print(f"  beat {index + 1}/{len(SCRIPT)}  {'+'.join(beat.keys) or 'start':<12} {beat.hold}s")

    return timeline


def assemble(timeline: list[tuple[Path, float]], out: Path, scale: float) -> None:
    """Stitch the frames with ffmpeg, through a generated palette.

    palettegen/paletteuse rather than ffmpeg's default quantiser: a terminal is
    mostly flat colour with thin bright text, and the default palette turns the
    text into dithered mud.
    """
    listing = out.parent / "frames.txt"
    lines = []
    for path, hold in timeline:
        lines.append(f"file '{path.as_posix()}'")
        lines.append(f"duration {hold:.3f}")
    lines.append(f"file '{timeline[-1][0].as_posix()}'")  # concat needs the last one twice
    listing.write_text("\n".join(lines), encoding="utf-8")

    width = f"iw*{scale}" if scale != 1 else "iw"
    chain = (
        f"fps=20,scale={width}:-1:flags=lanczos,"
        "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3"
    )
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-filter_complex",
        chain,
        "-loop",
        "0",
        str(out),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    listing.unlink(missing_ok=True)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{result.stderr[-2000:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ASSETS / "demo.gif")
    parser.add_argument("--scale", type=float, default=1.0, help="output scale, e.g. 0.75")
    parser.add_argument("--keep-frames", action="store_true", help="leave the PNGs behind")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    frames = args.out.parent / "_frames"
    frames.mkdir(exist_ok=True)

    font = load_font()
    cell_width = round(font.getlength("M"))
    ascent, descent = font.getmetrics()
    cell = (cell_width, ascent + descent)
    print(f"font cell {cell[0]}x{cell[1]}px, screen {COLUMNS}x{ROWS} cells")

    timeline = asyncio.run(record(frames, font, cell))
    assemble(timeline, args.out, args.scale)

    if not args.keep_frames:
        for path in frames.glob("frame_*.png"):
            path.unlink()
        frames.rmdir()

    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size / 1024:.0f} KB, {sum(hold for _, hold in timeline):.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
