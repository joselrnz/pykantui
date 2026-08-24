"""Assemble privacy-safe README GIFs from synthetic provider captures."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from PIL import Image

FRAME_PHASES = (
    "before",
    "create-local",
    "markdown-edit",
    "tui-edit",
    "move",
    "comment-draft",
    "sync-result",
    "api-validated",
    "conflict",
)
PROVIDERS = ("asana", "clickup", "forgejo", "github", "jira", "linear", "monday", "plane", "shortcut", "trello")
FRAME_DURATION_MS = 1_500


def _frame_paths(evidence_root: Path, run_tag: str, provider: str) -> tuple[Path, ...]:
    base = evidence_root / run_tag / provider
    return tuple(base / f"{number:02d}-{phase}.png" for number, phase in enumerate(FRAME_PHASES, start=1))


def _load_frames(paths: Iterable[Path]) -> list[Image.Image]:
    frames: list[Image.Image] = []
    expected_size: tuple[int, int] | None = None
    for path in paths:
        if not path.is_file():
            raise ValueError(f"missing frame: {path.name}")
        with Image.open(path) as source:
            frame = source.convert("RGB")
        if expected_size is None:
            expected_size = frame.size
        elif frame.size != expected_size:
            raise ValueError(f"frame geometry differs: {path.name}")
        frames.append(frame)
    if not frames:
        raise ValueError("at least one frame is required")
    return frames


def _save_gif(frames: Sequence[Image.Image], target: Path, *, duration: int = FRAME_DURATION_MS) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".gif", delete=False) as stream:
            temporary = Path(stream.name)
        frames[0].save(
            temporary,
            format="GIF",
            save_all=True,
            append_images=list(frames[1:]),
            duration=duration,
            loop=0,
            disposal=2,
            optimize=False,
        )
        temporary.replace(target)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return target


def build_provider_gif(evidence_root: Path, run_tag: str, provider: str, target: Path) -> Path:
    """Build one nine-phase provider GIF with no source-image metadata."""

    return _save_gif(_load_frames(_frame_paths(evidence_root, run_tag, provider)), target)


def build_all(evidence_root: Path, run_tag: str, assets: Path) -> tuple[Path, ...]:
    """Build every README provider GIF and privacy-safe aggregate assets."""

    outputs: list[Path] = []
    combined: list[Image.Image] = []
    timeline: list[Image.Image] = []
    nested = assets / "live-real-9x1"
    for provider in PROVIDERS:
        paths = _frame_paths(evidence_root, run_tag, provider)
        frames = _load_frames(paths)
        target = _save_gif(frames, assets / f"live-real-9x1-{provider}.gif")
        outputs.append(target)
        nested_target = nested / target.name
        nested_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, nested_target)
        outputs.append(nested_target)
        combined.extend(frames)
        timeline.append(frames[0])
    outputs.append(_save_gif(combined, assets / "live-real-9x1.gif", duration=900))
    outputs.append(_save_gif(timeline, assets / "demo-providers-timeline.gif", duration=1_800))
    return tuple(outputs)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=Path("artifacts/provider-evidence"))
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--assets", type=Path, default=Path("assets"))
    arguments = parser.parse_args(argv)
    for path in build_all(arguments.evidence_root, arguments.run_tag, arguments.assets):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
