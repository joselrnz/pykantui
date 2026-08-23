"""Build the restrained interactive README/PyPI banner GIF.

The checked-in PNG remains the art source. This script adds only deterministic
terminal motion: a typed sync command, keyboard-style focus moving across the
board, and a compact completion message.

    python tools/banner_gif.py
    python tools/banner_gif.py --width 1400 --out assets/pykantui-banner-v3.gif
"""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DEFAULT_SOURCE = ASSETS / "pykantui-banner-v2.png"
DEFAULT_OUTPUT = ASSETS / "pykantui-banner-v3.gif"

OFF_WHITE = (224, 230, 236)
SLATE = (111, 130, 148)
CYAN = (0, 214, 232)

FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\CascadiaMono.ttf"),
    Path(r"C:\Windows\Fonts\consola.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("/System/Library/Fonts/Menlo.ttc"),
)

HIGHLIGHT_ORDER = ("todo", "doing", "review", "done")
CARD_RECTS = {
    "todo": (944, 240, 1164, 337),
    "doing": (1191, 240, 1407, 337),
    "review": (1434, 240, 1647, 337),
    "done": (1674, 240, 1889, 337),
}
REVIEW_LABEL_MAX_WIDTH = 170
REVIEW_LABEL_Y = 265


@dataclass(frozen=True)
class BannerFrame:
    """One intentionally paced state in the looping banner."""

    command: str = ""
    highlight: str | None = None
    status: str = ""
    cursor: bool = True
    duration_ms: int = 300


def build_timeline() -> tuple[BannerFrame, ...]:
    """Return the small, readable interaction story shown by the banner."""
    return (
        BannerFrame(duration_ms=550),
        BannerFrame(cursor=False, duration_ms=180),
        BannerFrame(command="pyk", duration_ms=130),
        BannerFrame(command="pykantui", duration_ms=180),
        BannerFrame(command="pykantui sync", duration_ms=500),
        BannerFrame(command="pykantui sync", highlight="todo", status="reading local task", duration_ms=480),
        BannerFrame(command="pykantui sync", highlight="doing", status="mapping provider fields", duration_ms=480),
        BannerFrame(command="pykantui sync", highlight="review", status="checking remote state", duration_ms=480),
        BannerFrame(command="pykantui sync", highlight="done", status="writing local state", duration_ms=650),
        BannerFrame(status="synced 1 task", duration_ms=950),
    )


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("no supported monospace font found")


def fit_label_font(
    label: str,
    *,
    max_width: int,
    preferred_size: int = 22,
    minimum_size: int = 12,
) -> ImageFont.FreeTypeFont:
    """Return the largest supported font that keeps a card label contained."""
    if max_width <= 0:
        raise ValueError("max_width must be positive")
    if minimum_size <= 0 or preferred_size < minimum_size:
        raise ValueError("font size range is invalid")

    for size in range(preferred_size, minimum_size - 1, -1):
        font = _load_font(size)
        if font.getlength(label) <= max_width:
            return font
    raise ValueError(f"label does not fit within {max_width}px: {label!r}")


def project_version() -> str:
    """Read the release version so the banner never advertises a stale tag."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return str(project["version"])


def _render_frame(
    source: Image.Image,
    state: BannerFrame,
    font: ImageFont.FreeTypeFont,
    release_font: ImageFont.FreeTypeFont,
    release_label: str,
) -> Image.Image:
    frame = source.copy()
    draw = ImageDraw.Draw(frame)

    # The source contains a single idle caret. Replace only that narrow strip
    # with neighboring background pixels so the PNG's subtle texture remains
    # intact instead of introducing a visible flat rectangle.
    frame.paste(source.crop((171, 452, 191, 552)), (151, 452))
    command_origin = (159, 466)
    draw.text(command_origin, state.command, font=font, fill=OFF_WHITE)
    command_width = draw.textlength(state.command, font=font)
    if state.cursor:
        cursor_x = round(command_origin[0] + command_width + 4)
        draw.line((cursor_x, 467, cursor_x, 500), fill=OFF_WHITE, width=3)

    if state.status:
        marker = "✓ " if state.status == "synced 1 task" else "· "
        color = CYAN if state.status == "synced 1 task" else SLATE
        draw.text((159, 516), marker + state.status, font=font, fill=color)

    # Keep the release card synchronized with pyproject.toml even though the
    # rest of the board is inherited from the hand-refined PNG source.
    review_fill = source.getpixel((1635, 275))
    draw.rectangle((1447, 253, 1638, 292), fill=review_fill)
    review_left, _, review_right, _ = CARD_RECTS["review"]
    label_width = draw.textlength(release_label, font=release_font)
    label_x = round((review_left + review_right - label_width) / 2)
    draw.text((label_x, REVIEW_LABEL_Y), release_label, font=release_font, fill=OFF_WHITE)

    if state.highlight:
        box = CARD_RECTS[state.highlight]
        draw.rounded_rectangle(box, radius=9, outline=CYAN, width=4)
        x1, y1, _, _ = box
        draw.polygon(((x1 - 15, y1 + 39), (x1 - 5, y1 + 48), (x1 - 15, y1 + 57)), fill=CYAN)

    return frame


def build_banner_gif(source_path: Path, output_path: Path, *, width: int = 1400) -> Path:
    """Render a looping, palette-optimized GIF from the banner PNG."""
    if width <= 0:
        raise ValueError("width must be positive")

    with Image.open(source_path) as opened:
        source = opened.convert("RGB")

    font = _load_font(29)
    release_version = project_version()
    release_label = f"[ ] Release {release_version}"
    release_font = fit_label_font(release_label, max_width=REVIEW_LABEL_MAX_WIDTH)
    timeline = build_timeline()
    height = round(source.height * width / source.width)
    frames = [
        _render_frame(source, state, font, release_font, release_label).resize(
            (width, height), Image.Resampling.LANCZOS
        )
        for state in timeline
    ]

    palette = frames[0].convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
    indexed = [palette, *(frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames[1:])]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(
        output_path,
        save_all=True,
        append_images=indexed[1:],
        duration=[state.duration_ms for state in timeline],
        loop=0,
        disposal=1,
        optimize=False,
    )
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--width", type=int, default=1400)
    args = parser.parse_args()

    output = build_banner_gif(args.source, args.out, width=args.width)
    print(f"wrote {output} ({output.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
