"""Capture Jira's genuine expanded JQL filter bar from Textual."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from textual.widgets import Button, Input

from pykantui.models import BoardLayout
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker import get
from pykantui.tracker.base import Provider
from pykantui.tracker.models import RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tui.app import KanbanApp
from pykantui.workspace.sync import sync

SIZE = (160, 42)
PROJECT = RemoteProject(project_id="10002", key="JPT", name="Jira filter demo")
TODO = RemoteColumn(column_id="10000", name="To Do", position=0, group="todo")
DOING = RemoteColumn(column_id="10001", name="In Progress", position=1, group="started")
DONE = RemoteColumn(column_id="10002", name="Done", position=2, group="done")


class OfflineJiraProvider(Provider):
    """Real Jira capability contract with deterministic, offline issue data."""

    spec = get("jira").spec

    def __init__(self) -> None:
        super().__init__({"jql": 'statusCategory != Done'}, {})
        self._issues = [
            RemoteIssue(
                issue_id=str(index),
                key=f"JPT-{index}",
                title=title,
                column_id=column.column_id,
                status=column.name,
                issue_type=issue_type,
                priority=priority,
                assignee="Jose Lorenzo",
            )
            for index, title, column, issue_type, priority in (
                (31, "Refine provider-aware filters", TODO, "Story", "High"),
                (32, "Verify JQL cache bypass", DOING, "Task", "Medium"),
                (33, "Keep local drafts during search", DOING, "Bug", "Highest"),
                (34, "Document read-only query overlay", DONE, "Task", "Low"),
            )
        ]

    def verify(self) -> RemoteUser:
        return RemoteUser(display_name="Jose Lorenzo")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return [TODO, DOING, DONE]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(self._issues)


def _rasterise(svg_path: Path) -> Path | None:
    try:
        import resvg_py
    except ImportError:
        pass
    else:
        png_path = svg_path.with_suffix(".png")
        png_path.write_bytes(resvg_py.svg_to_bytes(svg_path=str(svg_path)))
        return png_path

    discovered = shutil.which("chrome") or shutil.which("google-chrome")
    candidates = (
        Path(discovered) if discovered else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    )
    renderer = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
    if renderer is None:
        return None

    view_box = svg_path.read_text(encoding="utf-8").split('viewBox="', 1)[1].split('"', 1)[0]
    _x, _y, width, height = (round(float(value)) for value in view_box.split())
    png_path = svg_path.with_suffix(".png")
    png_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="pykantui-jql-button-shot-") as profile:
        arguments = (
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            f"--user-data-dir={profile}",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            f"--screenshot={png_path.resolve()}",
            svg_path.resolve().as_uri(),
        )
        escaped = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        command = (
            f"$p=Start-Process -FilePath '{renderer}' -ArgumentList @({escaped}) "
            "-Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    return png_path if not result.returncode and png_path.is_file() else None


async def render(into: Path) -> tuple[Path, ...]:
    """Render and validate every visible Jira query-button state."""
    into.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    metrics: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        provider = OfflineJiraProvider()
        sync(workspace, provider, PROJECT, push_edits=False, commit=False)
        app = KanbanApp(ProviderBackend(workspace, provider, PROJECT), confirm_moves=False)

        async with app.run_test(size=SIZE) as pilot:
            app.theme = "cyberpunk"
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()

            query = app.menu_bar.query_one("#filter-query", Input)
            search = app.menu_bar.query_one("#filter-search", Button)
            if query.disabled or search.disabled:
                raise RuntimeError("Jira JQL controls are unexpectedly disabled")
            query.value = 'status = "In Progress"'
            query.focus()
            await pilot.pause()
            if not query.has_focus:
                raise RuntimeError("JQL input did not receive focus")
            if app.backend.display_kind() != "Jira":
                raise RuntimeError("capture is not using Jira's provider contract")

            def capture(name: str) -> None:
                border = search.styles.border.top
                if str(border[0]) != "round":
                    raise RuntimeError(f"{name}: Search action is not a pill")
                if search.styles.background.a != 0:
                    raise RuntimeError(f"{name}: Search button paints its rounded corners")
                if search.region.right > app.menu_bar.content_region.right:
                    raise RuntimeError(f"{name}: Search button overflows the filter bar")
                svg_path = into / f"{name}.svg"
                app.save_screenshot(str(svg_path.resolve()))
                generated.append(svg_path)
                metrics[name] = {
                    "terminal_cells": [app.size.width, app.size.height],
                    "region": [
                        search.region.x,
                        search.region.y,
                        search.region.width,
                        search.region.height,
                    ],
                    "border_style": str(border[0]),
                    "border_color": border[1].hex,
                    "text_color": search.styles.color.hex,
                    "background": search.styles.background.hex,
                    "bold": search.styles.text_style.bold,
                    "reverse": search.styles.text_style.reverse,
                    "focused": search.has_focus,
                    "hovered": search.is_mouse_over,
                    "active": search.has_class("-active"),
                }

            capture("jira-expanded-jql-filter")
            await pilot.hover(search)
            await pilot.pause()
            capture("jira-jql-search-round-hover")

            await pilot.hover(query)
            search.focus()
            await pilot.pause()
            capture("jira-jql-search-round-focus")

            search.add_class("-active")
            await pilot.pause()
            capture("jira-jql-search-round-pressed")
            search.remove_class("-active")

            app.screen.add_class("edges-square")
            await pilot.pause()
            capture("jira-jql-search-edge-square-pill-focus")

    metrics_path = into / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    for svg_path in tuple(generated):
        png_path = _rasterise(svg_path)
        if png_path is not None:
            generated.append(png_path)
    generated.append(metrics_path)
    return tuple(generated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--into",
        type=Path,
        default=Path("artifacts/jira-query"),
        help="output directory (default: artifacts/jira-query)",
    )
    arguments = parser.parse_args()
    for path in asyncio.run(render(arguments.into)):
        print(path.resolve())
