"""Capture real row actions and Split editing from an offline Jira-shaped board.

The SVG is produced by Textual's compositor.  When a compatible SVG renderer
is available, an exact PNG rasterisation is written beside it for clients that
cannot preview SVG files directly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from datetime import date
from pathlib import Path

# Codex and some CI shells deliberately export NO_COLOR/TERM=dumb.  This tool
# captures the application's visual theme, so opt this isolated process back
# into the same true-colour capability an interactive terminal advertises.
os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from tests.integration.sync.test_push import DOING, TODO, RecordingProvider, issue
from textual.widgets import DataTable, Input, Tab, TabbedContent, TextArea

from pykantui.models import BoardLayout
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.menu import ContextMenuScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker import get
from pykantui.tracker.models import RemoteProject
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.sync import sync

SIZE = (160, 46)
PROJECT = RemoteProject(
    project_id="jira-sidebar-demo",
    key="SCRUM",
    name="Local-first sidebar demo",
)


def _backend(workspace: Path) -> ProviderBackend:
    provider = RecordingProvider(
        [
            issue(
                "SCRUM-25",
                TODO,
                title="Refine local-first sync confirmation",
                body="Explain exactly what stays in Markdown and what Sync sends to Jira.",
                assignee="Jose Lorenzo",
                issue_type="Story",
                priority="High",
                labels=("docs", "sync"),
                components=("Desktop", "Provider API"),
                due_date=date(2026, 8, 21),
                reporter="Platform team",
                parent_key="SCRUM-10",
                url="https://example.invalid/browse/SCRUM-25",
            ),
            issue(
                "SCRUM-24",
                DOING,
                title="Validate Markdown conflict recovery",
                body="Keep a second row visible so the split layout is unambiguous.",
                assignee="Alex",
                issue_type="Task",
                priority="Medium",
                labels=("edge-case",),
            ),
        ]
    )
    provider.spec = get("jira").spec  # type: ignore[misc]
    sync(workspace, provider, PROJECT, push_edits=False, commit=False)
    return ProviderBackend(workspace, provider, PROJECT)


def _assert_inline_editor(app: KanbanApp, root_screen: object, stack_size: int) -> WorkItemsView:
    view = app.query_one(WorkItemsView)
    if app.screen is not root_screen or len(app.screen_stack) != stack_size:
        raise RuntimeError("Split edit pushed a new screen")
    if app.screen.query("#detail-dialog"):
        raise RuntimeError("Split edit mounted the legacy detail dialog")
    if not view.editing:
        raise RuntimeError("Split edit did not activate the inline sidebar editor")
    return view


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
    else:
        return png_path


async def render(into: Path) -> tuple[Path, ...]:
    """Render the row menu, inline tabs, and explicit double-click popup."""
    into.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    with tempfile.TemporaryDirectory() as directory:
        app = KanbanApp(_backend(Path(directory)), confirm_moves=False)
        async with app.run_test(size=SIZE) as pilot:
            app.theme = "cyberpunk"
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            target_row = next(
                index
                for index, task in enumerate(app.visible_tasks())
                if task.metadata.get("key") == "SCRUM-25"
            )
            view.query_one(DataTable).move_cursor(row=target_row)
            await pilot.pause()
            selected = view.selected_task()
            if selected is None or selected.metadata.get("key") != "SCRUM-25":
                raise RuntimeError("the intended Jira work item was not selected")

            root_screen = app.screen
            stack_size = len(app.screen_stack)
            await pilot.click(
                "#work-items-table",
                offset=(8, target_row + 1),
                button=3,
            )
            await pilot.pause()
            if not isinstance(app.screen, ContextMenuScreen):
                raise RuntimeError("right-click did not open the row action menu")
            menu_svg = into / "row-context-menu.svg"
            app.save_screenshot(str(menu_svg))
            generated.append(menu_svg)

            await pilot.press("down", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            _assert_inline_editor(app, root_screen, stack_size)

            app.query_one("#work-item-edit-summary", Input).value = (
                "Refine local-first sync confirmation"
            )
            app.query_one("#work-item-edit-description", TextArea).load_text(
                "Explain exactly what stays in Markdown and what Sync sends to Jira."
            )
            app.query_one("#work-item-edit-private-notes", TextArea).load_text(
                "Local review: verify the conflict copy before provider Sync."
            )
            await pilot.pause()

            info_svg = into / "sidebar-inline-edit-info.svg"
            app.save_screenshot(str(info_svg))
            generated.append(info_svg)

            view.action_focus_tab("details")
            await pilot.pause()
            tabs = app.query_one("#work-item-tabs", TabbedContent)
            if tabs.active != "work-item-details-tab":
                available = [(tab.id, str(tab.label)) for tab in tabs.query(Tab)]
                raise RuntimeError(f"Details tab did not activate: {tabs.active!r}; tabs={available!r}")
            _assert_inline_editor(app, root_screen, stack_size)
            details_svg = into / "sidebar-inline-edit-details.svg"
            app.save_screenshot(str(details_svg))
            generated.append(details_svg)

            await view.cancel_inline_edit()
            await pilot.pause()
            await pilot.click(
                "#work-items-table",
                offset=(8, target_row + 1),
                times=2,
            )
            await pilot.pause()
            if not isinstance(app.screen, TaskDetailScreen) or app.screen.editing:
                raise RuntimeError("double-click did not open the read-only detail popup")
            popup_svg = into / "row-double-click-detail.svg"
            app.save_screenshot(str(popup_svg))
            generated.append(popup_svg)
            await pilot.press("escape")

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
