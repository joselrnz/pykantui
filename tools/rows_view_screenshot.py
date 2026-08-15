"""Capture real Rows-view mouse actions from an offline Jira-shaped board.

Textual's compositor writes each SVG.  When a compatible SVG renderer is
installed, the tool writes an exact PNG rasterisation beside it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

# Screenshot tools often run under CI-like shells that export NO_COLOR or
# TERM=dumb.  This isolated process intentionally captures the same true-colour
# cyberpunk theme that an interactive terminal presents.
os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from textual.containers import Vertical
from textual.widgets import DataTable

from pykantui.models import BoardLayout
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.menu import ContextMenuScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker import get
from pykantui.tracker.base import Provider
from pykantui.tracker.models import RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.sync import sync

SIZE = (160, 46)
TARGET_KEY = "SCRUM-24"
PROJECT = RemoteProject(
    project_id="jira-rows-demo",
    key="SCRUM",
    name="Rows interaction demo",
)
TODO = RemoteColumn(column_id="c-todo", name="To Do", position=0, group="todo")
DOING = RemoteColumn(column_id="c-doing", name="In Progress", position=1, group="started")
REVIEW = RemoteColumn(column_id="c-review", name="In Review", position=2, group="review")
DONE = RemoteColumn(column_id="c-done", name="Done", position=3, group="done")
COLUMNS = [TODO, DOING, REVIEW, DONE]


def _issue(key: str, column: RemoteColumn, **values: object) -> RemoteIssue:
    data: dict[str, object] = {
        "issue_id": f"id-{key}",
        "key": key,
        "title": f"Title {key}",
        "column_id": column.column_id,
        "status": column.name,
        "body": f"Body {key}",
    }
    data.update(values)
    return RemoteIssue(**data)  # type: ignore[arg-type]


class OfflineJiraProvider(Provider):
    """Production provider contract backed by fixed in-memory Jira data."""

    spec = get("jira").spec

    def __init__(self, issues: list[RemoteIssue]) -> None:
        super().__init__({}, {})
        self._issues = issues

    def verify(self) -> RemoteUser:
        return RemoteUser(display_name="Screenshot user")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return COLUMNS

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(self._issues)

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        return next((candidate for candidate in self._issues if candidate.issue_id == issue.issue_id), None)


def _backend(workspace: Path) -> ProviderBackend:
    """Build a Jira-shaped provider without network or credentials."""
    provider = OfflineJiraProvider(
        [
            _issue(
                "SCRUM-25",
                TODO,
                title="Refine local-first sync confirmation",
                body="Document the exact Markdown and provider boundaries.",
                assignee="Jose Lorenzo",
                issue_type="Story",
                priority="Highest",
                labels=("docs", "sync"),
                components=("Desktop",),
                due_date=date(2026, 8, 21),
            ),
            _issue(
                TARGET_KEY,
                DOING,
                title="Validate row mouse interactions",
                body="Right-click offers View and Edit; double-click opens View.",
                assignee="Jose Lorenzo",
                issue_type="Task",
                priority="High",
                labels=("tui", "edge-case"),
                components=("Rows view", "Provider API"),
                due_date=date(2026, 8, 18),
                reporter="Platform team",
                url="https://example.invalid/browse/SCRUM-24",
            ),
            _issue(
                "SCRUM-23",
                REVIEW,
                title="Review conflict recovery wording",
                body="Keep the local-first conflict choices explicit.",
                assignee="Alex Rivera",
                issue_type="Bug",
                priority="Medium",
                labels=("conflict",),
                components=("Sync engine",),
            ),
            _issue(
                "SCRUM-22",
                DONE,
                title="Preserve private Markdown notes",
                body="Provider payloads must never include local-only notes.",
                assignee="Morgan Chen",
                issue_type="Task",
                priority="Low",
                labels=("markdown", "security"),
                components=("Workspace",),
            ),
        ]
    )
    sync(workspace, provider, PROJECT, push_edits=False, commit=False)
    return ProviderBackend(workspace, provider, PROJECT)


def _assert_rows_layout(app: KanbanApp) -> WorkItemsView:
    """Prove the table is the full-width Rows layout behind every capture."""
    if app.board_layout is not BoardLayout.ROWS:
        raise RuntimeError(f"expected Rows layout, got {app.board_layout!r}")
    view = app.query_one(WorkItemsView)
    if not view.display:
        raise RuntimeError("Rows view is hidden")
    if view.detail_visible:
        raise RuntimeError("Rows layout unexpectedly shows the Split detail pane")
    list_pane = view.query_one("#work-items-list-pane", Vertical)
    if list_pane.region.width != view.content_region.width:
        raise RuntimeError(
            "Rows table is not full width: "
            f"list={list_pane.region.width}, available={view.content_region.width}"
        )
    return view


def _target_row(app: KanbanApp) -> int:
    """Return the rendered row index of the intended Jira issue."""
    try:
        return next(
            index
            for index, task in enumerate(app.visible_tasks())
            if task.metadata.get("key") == TARGET_KEY
        )
    except StopIteration:
        raise RuntimeError(f"{TARGET_KEY} is not visible in Rows view") from None


def _assert_selected(view: WorkItemsView) -> None:
    selected = view.selected_task()
    if selected is None or selected.metadata.get("key") != TARGET_KEY:
        actual = None if selected is None else selected.metadata.get("key")
        raise RuntimeError(f"wrong row selected: expected {TARGET_KEY}, got {actual!r}")


def _assert_popup(app: KanbanApp, *, editing: bool) -> TaskDetailScreen:
    screen = app.screen
    if not isinstance(screen, TaskDetailScreen):
        raise RuntimeError(f"row action opened {type(screen).__name__}, not TaskDetailScreen")
    if screen.editing is not editing:
        state = "edit" if editing else "read-only"
        raise RuntimeError(f"row action did not open the {state} popup")
    if screen.task_.metadata.get("key") != TARGET_KEY:
        raise RuntimeError(
            f"popup has {screen.task_.metadata.get('key')!r}, expected {TARGET_KEY}"
        )
    return screen


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
    """Capture menu, Edit popup, and double-click View popup in Rows."""
    into.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    with tempfile.TemporaryDirectory() as directory:
        app = KanbanApp(_backend(Path(directory)), confirm_moves=False)
        async with app.run_test(size=SIZE) as pilot:
            app.theme = "cyberpunk"
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            view = _assert_rows_layout(app)
            target_row = _target_row(app)
            table = view.query_one("#work-items-table", DataTable)
            table.move_cursor(row=target_row)
            await pilot.pause()
            _assert_selected(view)
            root_screen = app.screen
            root_stack_size = len(app.screen_stack)

            await pilot.click(
                "#work-items-table",
                offset=(8, target_row + table.header_height),
                button=3,
            )
            await pilot.pause()
            _assert_rows_layout(app)
            if not isinstance(app.screen, ContextMenuScreen):
                raise RuntimeError("right-click did not open the row action menu")
            if [item.label for item in app.screen.items] != ["View", "Edit"]:
                raise RuntimeError(f"unexpected row actions: {app.screen.items!r}")
            _assert_selected(view)
            menu_svg = into / "rows-right-click-menu.svg"
            app.save_screenshot(str(menu_svg))
            generated.append(menu_svg)

            await pilot.press("down", "enter")
            await pilot.pause()
            _assert_rows_layout(app)
            _assert_popup(app, editing=True)
            if len(app.screen_stack) != root_stack_size + 1:
                raise RuntimeError("row-menu Edit stacked an unexpected number of screens")
            edit_svg = into / "rows-menu-edit-popup.svg"
            app.save_screenshot(str(edit_svg))
            generated.append(edit_svg)

            await pilot.press("escape")
            await app.workers.wait_for_complete()
            await pilot.pause()
            if app.screen is not root_screen or len(app.screen_stack) != root_stack_size:
                raise RuntimeError("closing row-menu Edit did not return to Rows")
            view = _assert_rows_layout(app)
            _assert_selected(view)

            await pilot.click(
                "#work-items-table",
                offset=(8, target_row + table.header_height),
                times=2,
            )
            await pilot.pause()
            _assert_rows_layout(app)
            _assert_popup(app, editing=False)
            if len(app.screen_stack) != root_stack_size + 1:
                raise RuntimeError("double-click stacked an unexpected number of screens")
            view_svg = into / "rows-double-click-view-popup.svg"
            app.save_screenshot(str(view_svg))
            generated.append(view_svg)
            await pilot.press("escape")

    for svg_path in tuple(generated):
        png_path = _rasterise(svg_path)
        if png_path is not None:
            generated.append(png_path)
    return tuple(generated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--into",
        type=Path,
        default=Path("artifacts/rows-view"),
        help="output directory (default: artifacts/rows-view)",
    )
    arguments = parser.parse_args()
    for path in asyncio.run(render(arguments.into)):
        print(path.resolve())
