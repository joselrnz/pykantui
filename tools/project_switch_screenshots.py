"""Capture genuine Textual renders of registered-workspace switching.

The generated SVG files come directly from Textual's compositor.  A PNG is
written beside each SVG when the optional renderer used by the other visual
regression tools is installed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.models import Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.workspace.project import Project
from pykantui.workspace.registry import ProjectLink, ProjectRegistry

SIZE = (150, 40)


class DirtyScreenshotBackend(JsonBackend):
    """Offline board with one unsent edit for the confirmation render."""

    supports_sync = True

    def __init__(self) -> None:
        super().__init__(
            config=BoardConfig(
                columns=[
                    ColumnConfig(column_id=1, name="To Do", position=0),
                    ColumnConfig(column_id=2, name="Done", position=1),
                ],
                reset_column=1,
                start_column=1,
                finish_column=2,
            )
        )
        self.create_task(
            Task(
                task_id=1,
                title="Document the local-first workflow",
                column_id=1,
                metadata={"sync_status": "edited"},
            )
        )


def _workspace(
    root: Path,
    folder: str,
    *,
    provider: str,
    project_id: str,
    key: str,
    name: str,
) -> ProjectLink:
    workspace = root / folder
    workspace.mkdir(parents=True)
    Project(provider=provider, project_id=project_id, key=key, name=name).save(workspace)
    return ProjectLink(
        provider=provider,
        project_id=project_id,
        key=key,
        name=name,
        workspace=str(workspace.resolve()),
    )


def _rasterise(svg_path: Path) -> Path | None:
    png_path = svg_path.with_suffix(".png")
    try:
        import resvg_py
    except ImportError:
        pass
    else:
        png_path.write_bytes(resvg_py.svg_to_bytes(svg_path=str(svg_path)))
        return png_path

    try:
        import cairosvg
    except (ImportError, OSError):
        return None
    try:
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
    except OSError:
        return None
    return png_path


async def render(into: Path) -> tuple[Path, ...]:
    """Render the duplicate-name chooser and dirty-workspace guard."""

    into.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        links = [
            _workspace(
                root,
                "jira application",
                provider="jira",
                project_id="10001",
                key="APP",
                name="Roadmap",
            ),
            _workspace(
                root,
                "github application",
                provider="github",
                project_id="acme/api",
                key="acme/api",
                name="Roadmap",
            ),
            _workspace(
                root,
                "study schedule",
                provider="asana",
                project_id="1200123456789",
                key="STUDY",
                name="Study Schedule",
            ),
            _workspace(
                root,
                "operations",
                provider="monday",
                project_id="456789",
                key="OPS",
                name="Operations",
            ),
            _workspace(
                root,
                "Planificación 国際",
                provider="linear",
                project_id="team-product",
                key="PRODUCT",
                name="Product Launch",
            ),
        ]
        registry = ProjectRegistry(projects=links)

        chooser_app = KanbanApp(DirtyScreenshotBackend(), confirm_moves=False)
        with patch("pykantui.tui.controllers.projects.load_registry", return_value=registry):
            async with chooser_app.run_test(size=SIZE) as pilot:
                chooser_app.theme = "cyberpunk"
                await pilot.pause()
                chooser_app.action_projects()
                await pilot.pause()
                chooser_svg = into / "projects-picker.svg"
                chooser_app.save_screenshot(str(chooser_svg))
                generated.append(chooser_svg)

        confirm_app = KanbanApp(DirtyScreenshotBackend(), confirm_moves=False)
        single = ProjectRegistry(projects=[links[0]])
        with patch("pykantui.tui.controllers.projects.load_registry", return_value=single):
            async with confirm_app.run_test(size=SIZE) as pilot:
                confirm_app.theme = "cyberpunk"
                await pilot.pause()
                confirm_app.action_projects()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                confirm_svg = into / "projects-dirty-confirm.svg"
                confirm_app.save_screenshot(str(confirm_svg))
                generated.append(confirm_svg)

    for svg_path in tuple(generated):
        png_path = _rasterise(svg_path)
        if png_path is not None:
            generated.append(png_path)
    return tuple(generated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, required=True)
    arguments = parser.parse_args()
    for path in asyncio.run(render(arguments.into)):
        print(path.resolve())
