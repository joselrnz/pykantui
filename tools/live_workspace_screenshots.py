"""Capture real Textual screenshots from isolated live-provider workspaces.

The command reads provider columns so the configured board can be rendered,
but never calls Sync or any provider mutation. Captures are filtered to the
exact run tag and emitted as paired SVG/PNG files.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

from provider_evidence import rasterise_svg, validate_png, validate_svg

from pykantui.models.enums import BoardLayout
from pykantui.sync.provider import ProviderBackend
from pykantui.tui.app import KanbanApp
from pykantui.workspace.project import Project

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_PROVIDERS = ("asana", "clickup", "github", "jira", "linear", "monday", "plane", "shortcut", "trello")


def safe_name(value: str, *, label: str) -> str:
    if not _SAFE.fullmatch(value):
        raise ValueError(f"{label} is not a safe artifact name")
    return value


def _receipt(path: Path, root: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


async def capture_provider(
    workspace: Path,
    output_root: Path,
    *,
    run_tag: str,
    stage: str,
    svg_only: bool = False,
) -> dict[str, object]:
    project = Project.load(workspace)
    provider = project.open()
    target = output_root / run_tag / "live-local" / project.provider
    target.mkdir(parents=True, exist_ok=True)
    captures: list[dict[str, object]] = []
    try:
        backend = ProviderBackend(workspace, provider, project.remote())
        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.view.card_filter.text = run_tag
            await app.action_refresh_board()
            await pilot.pause()
            for layout in (BoardLayout.KANBAN, BoardLayout.ROWS, BoardLayout.SPLIT):
                app.set_board_layout(layout)
                await pilot.pause()
                svg = target / f"{stage}-{layout.value}.svg"
                app.title = f"{project.provider.title()} · {stage} · {run_tag}"
                app.save_screenshot(str(svg.resolve()))
                capture: dict[str, object] = {
                    "layout": layout.value,
                    "svg": _receipt(svg, output_root),
                    "svg_geometry": validate_svg(svg),
                }
                if not svg_only:
                    png = rasterise_svg(svg)
                    capture["png"] = _receipt(png, output_root)
                    capture["png_geometry"] = validate_png(png)
                captures.append(capture)
    finally:
        provider.close()
    return {
        "provider": project.provider,
        "project_id": project.project_id,
        "stage": stage,
        "evidence_kind": "live-workspace-local-state",
        "provider_writes": 0,
        "captures": captures,
    }


async def capture_all(
    workspace_root: Path,
    output_root: Path,
    *,
    run_tag: str,
    stage: str,
    svg_only: bool = False,
) -> Path:
    safe_name(run_tag, label="run tag")
    safe_name(stage, label="stage")
    receipts = []
    for provider in _PROVIDERS:
        receipts.append(
            await capture_provider(
                workspace_root / provider,
                output_root,
                run_tag=run_tag,
                stage=stage,
                svg_only=svg_only,
            )
        )
    target = output_root / run_tag / "live-local" / f"{stage}.json"
    target.write_text(json.dumps(receipts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-provider-e2e"))
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--svg-only", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        capture_all(
            args.workspace_root.resolve(strict=True),
            args.output.resolve(),
            run_tag=args.run_tag,
            stage=args.stage,
            svg_only=args.svg_only,
        )
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
