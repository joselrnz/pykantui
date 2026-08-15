"""Capture real large-board filter results in Kanban, Rows, and Split.

The screenshots are produced by Textual's compositor from one deterministic
1,000-card, Jira-shaped board.  No provider network or credential is used.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, datetime, timedelta
from pathlib import Path

# Screenshot tools can inherit CI-style terminal settings.  This process is
# intentionally true-colour so it renders the production cyberpunk theme.
os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from textual.geometry import Offset
from textual.widgets import DataTable, Input, OptionList, Select

from pykantui.core.actions import Menu
from pykantui.core.filters import BoardView, CardFilter, FilterState
from pykantui.core.work_items import CORE_WORK_ITEM_COLUMNS, WorkItemColumn
from pykantui.models import Board, BoardLayout, Task
from pykantui.pages.menu import ContextMenuScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker import get
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.work_item_table import DetailField
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.status import SyncStatus

SIZE = (160, 46)
CARD_COUNT = 1_000
EXPECTED_MATCHES = 50


class ScaleBackend(JsonBackend):
    """In-memory Jira-shaped backend for a production-widget capture."""

    supports_issue_fields = True
    supports_sync = True
    supports_reorder = False

    def __init__(self) -> None:
        super().__init__()
        self._tasks = _tasks()

    def display_kind(self) -> str:
        return "Jira"

    def get_active_board(self) -> Board:
        return super().get_active_board().model_copy(update={"name": "1,000-card filter audit"})

    def provider_filter_fields(self):  # type: ignore[no-untyped-def]
        return get("jira").spec.filter_fields()

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        """Expose Jira's exact table fields without making any API call."""
        return get("jira").spec.available_table_fields({})


class UnsupportedTypeBackend(ScaleBackend):
    """Asana-shaped capability view over the same deterministic card set."""

    def __init__(self) -> None:
        super().__init__()
        self._tasks = [
            task.model_copy(
                update={
                    "title": task.title.replace("Jira scale item", "Asana scale item"),
                    "metadata": {
                        **task.metadata,
                        "id": str(task.metadata["id"]).replace("jira-scale", "asana-scale"),
                    },
                }
            )
            for task in self._tasks
        ]

    def display_kind(self) -> str:
        return "Asana"

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        return get("asana").spec.available_table_fields({})


def _tasks() -> list[Task]:
    """Return a repeatable mix of statuses, dates, fields, and sync states."""
    today = date.today()
    created = datetime(2025, 1, 1, 9)
    sync_states = tuple(SyncStatus)
    return [
        Task(
            task_id=index + 1,
            title=("release-target " if index % 20 == 0 else "ordinary ")
            + f"Jira scale item {index:04d}",
            column_id=((index // 20) % 5) + 1,
            position=index,
            description=f"Performance and filter verification {index:04d}",
            created_at=created + timedelta(hours=index),
            due_date=(today - timedelta(days=1), today, None, today + timedelta(days=7))[(index // 20) % 4],
            blocked_by=[CARD_COUNT + 1] if index % 13 == 0 else [],
            metadata={
                "id": f"jira-scale-{index:04d}",
                "key": f"LOAD-{index:04d}",
                "project": "LOAD",
                "assignee": "Alex" if (index // 20) % 2 == 0 else "Sam",
                "reporter": "Riley" if (index // 20) % 3 == 0 else "Morgan",
                "issue_type": "Bug" if index % 3 == 0 else "Task",
                "priority": ("Highest", "High", "Medium", "Low")[(index // 20) % 4],
                "labels": ["backend", "release"] if index % 4 == 0 else ["ui"],
                "sync_status": sync_states[(index // 20) % len(sync_states)].value,
                "url": f"https://example.test/browse/LOAD-{index:04d}",
            },
        )
        for index in range(CARD_COUNT)
    ]


def _filtered_view() -> BoardView:
    # Every twentieth card is a release target, and every such card has the
    # backend label.  That gives exactly 50 matches out of 1,000.
    return BoardView(
        card_filter=CardFilter(
            text="release-target",
            states=[FilterState.HAS_NOTES],
            provider={"labels": "backend"},
            project="LOAD",
        ),
        columns=[
            *CORE_WORK_ITEM_COLUMNS,
            WorkItemColumn.TYPE,
            WorkItemColumn.ASSIGNEE,
            WorkItemColumn.REPORTER,
        ],
    )


def _rasterise(svg_path: Path) -> Path | None:
    png_path = svg_path.with_suffix(".png")
    try:
        import resvg_py
    except ImportError:
        return None
    png_path.write_bytes(resvg_py.svg_to_bytes(svg_path=str(svg_path)))
    return png_path


async def render(into: Path) -> tuple[Path, ...]:
    """Render all three layouts and assert their exact filtered identities."""
    into.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    backend = ScaleBackend()
    app = KanbanApp(backend, confirm_moves=False)

    async with app.run_test(size=SIZE) as pilot:
        app.theme = "cyberpunk"
        # App startup deliberately constructs the clean default view. Install
        # the screenshot scenario only after mount so startup cannot replace it.
        app.view = _filtered_view()
        app.view.set_column_sort(WorkItemColumn.STATUS)
        await app.apply_view()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        expected_ids = [task.task_id for task in app.visible_tasks()]
        if len(expected_ids) != EXPECTED_MATCHES:
            raise RuntimeError(
                f"expected {EXPECTED_MATCHES} filtered cards, got {len(expected_ids)}"
            )

        for layout, filename in (
            (BoardLayout.KANBAN, "large-filter-kanban.svg"),
            (BoardLayout.ROWS, "large-filter-rows.svg"),
            (BoardLayout.SPLIT, "large-filter-split.svg"),
        ):
            app.set_board_layout(layout)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            actual_ids = [task.task_id for task in app.visible_tasks()]
            if actual_ids != expected_ids:
                raise RuntimeError(f"{layout.value} changed the filtered identities")

            if layout is BoardLayout.KANBAN:
                if len(app.query(TaskCard)) != EXPECTED_MATCHES:
                    raise RuntimeError("Kanban did not mount exactly the filtered cards")
            else:
                view = app.query_one(WorkItemsView)
                table = view.query_one(DataTable)
                if table.row_count != EXPECTED_MATCHES:
                    raise RuntimeError(
                        f"{layout.value} rendered {table.row_count} rows, expected {EXPECTED_MATCHES}"
                    )
                if view.detail_visible is not (layout is BoardLayout.SPLIT):
                    raise RuntimeError(f"{layout.value} detail-pane visibility is wrong")
                if layout is BoardLayout.ROWS:
                    labels = {str(column.label) for column in table.columns.values()}
                    required = {"Status ↑", "Type", "Assignee", "Reporter"}
                    if not required.issubset(labels):
                        raise RuntimeError(f"{layout.value} table lacks {required - labels}")
                if table.max_scroll_x:
                    raise RuntimeError(f"{layout.value} table scrolls horizontally")
                if layout is BoardLayout.SPLIT:
                    if not view.query_one("#work-item-status", DetailField).has_class(
                        "workflow-status-warning"
                    ):
                        raise RuntimeError("Split Status field lacks To Do semantic styling")
                    if not view.query_one("#work-item-issue-type", DetailField).has_class(
                        "work-item-type-error"
                    ):
                        raise RuntimeError("Split Type field lacks Bug semantic styling")
                    view.action_focus_tab("info")
                    await pilot.pause()

            if not app.view_summary().startswith(f"{EXPECTED_MATCHES} of {CARD_COUNT}"):
                raise RuntimeError(f"unexpected filter summary: {app.view_summary()!r}")

            svg_path = into / filename
            app.save_screenshot(str(svg_path))
            generated.append(svg_path)

            if layout is BoardLayout.ROWS:
                menu = ContextMenuScreen(
                    "Columns",
                    app._menu_items(Menu.COLUMNS),
                    anchor_at=Offset(55, 2),
                )
                await app.push_screen(menu)
                await pilot.pause()
                options = menu.query_one(OptionList)
                visible_options = " ".join(
                    str(options.get_option_at_index(index).prompt)
                    for index in range(options.option_count)
                )
                if "Assignee" not in visible_options or "Reporter" not in visible_options:
                    raise RuntimeError("Columns menu lacks provider people fields")
                columns_svg = into / "large-filter-rows-columns.svg"
                app.save_screenshot(str(columns_svg))
                generated.append(columns_svg)
                await app.pop_screen()
                await pilot.pause()

                # Capture the second half of the header contract too: the
                # same Status header flips from ascending to descending.
                app.view.set_column_sort(WorkItemColumn.STATUS)
                await app.apply_view()
                await app.workers.wait_for_complete()
                await pilot.pause()
                descending_labels = {
                    str(column.label) for column in table.columns.values()
                }
                if "Status ↓" not in descending_labels:
                    raise RuntimeError("Rows Status header lacks descending direction")
                descending_svg = into / "large-filter-rows-desc.svg"
                app.save_screenshot(str(descending_svg))
                generated.append(descending_svg)

                # Restore ascending before the Split identity/style capture.
                app.view.set_column_sort(WorkItemColumn.STATUS)
                await app.apply_view()
                await app.workers.wait_for_complete()
                await pilot.pause()

        # One genuine inline-sidebar capture proves the Status selector uses
        # the same semantic color without pushing a modal screen.
        split_view = app.query_one(WorkItemsView)
        await split_view.start_inline_edit()
        await pilot.pause()
        split_view.action_focus_tab("details")
        await pilot.pause()
        status_select = split_view.query_one("#work-item-edit-status", Select)
        if not status_select.has_class("workflow-status-warning"):
            raise RuntimeError("inline Status selector lacks To Do semantic styling")
        type_input = split_view.query_one("#work-item-edit-issue-type", Input)
        if not type_input.has_class("work-item-type-error"):
            raise RuntimeError("inline Type input lacks Bug semantic styling")
        edit_svg = into / "large-filter-split-edit.svg"
        app.save_screenshot(str(edit_svg))
        generated.append(edit_svg)

    asana_app = KanbanApp(UnsupportedTypeBackend(), confirm_moves=False)
    async with asana_app.run_test(size=SIZE) as pilot:
        asana_app.theme = "cyberpunk"
        asana_app.view = _filtered_view()
        asana_app.view.set_column_sort(WorkItemColumn.STATUS)
        asana_app.set_board_layout(BoardLayout.SPLIT)
        await asana_app.apply_view()
        await pilot.pause()
        asana_view = asana_app.query_one(WorkItemsView)
        asana_table = asana_view.query_one(DataTable)
        if WorkItemColumn.TYPE.value in asana_table.columns:
            raise RuntimeError("unsupported Asana Type column is visible")
        if asana_view.query_one("#work-item-issue-type", DetailField).display:
            raise RuntimeError("unsupported Asana Type detail is visible")
        required = {
            WorkItemColumn.STATUS.value,
            WorkItemColumn.ASSIGNEE.value,
            WorkItemColumn.REPORTER.value,
        }
        if not required.issubset(asana_table.columns):
            raise RuntimeError(f"Asana table lacks {required - set(asana_table.columns)}")
        asana_svg = into / "large-filter-asana-no-type.svg"
        asana_app.save_screenshot(str(asana_svg))
        generated.append(asana_svg)

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
        default=Path("artifacts/large-card-filter"),
        help="output directory (default: artifacts/large-card-filter)",
    )
    arguments = parser.parse_args()
    for path in asyncio.run(render(arguments.into)):
        print(path.resolve())
