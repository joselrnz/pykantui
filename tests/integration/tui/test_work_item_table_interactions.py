"""Mouse, responsive-column, and semantic-color integration for Rows/Split."""

from __future__ import annotations

import asyncio
import time
import unittest
from datetime import date
from unittest.mock import patch

from rich.style import Style
from rich.text import Text
from textual.events import Resize
from textual.geometry import Offset, Size
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import DataTable, Input, Select
from textual.widgets.data_table import ColumnKey

from pykantui.core.actions import Action, ActionKind
from pykantui.core.work_items import (
    CORE_WORK_ITEM_COLUMNS,
    OPTIONAL_WORK_ITEM_COLUMNS,
    WORK_ITEM_COLUMN_SPECS,
    WorkItemColumn,
)
from pykantui.models import BoardLayout, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.models import ColumnGroup
from pykantui.tui import provider_links
from pykantui.tui.app import KanbanApp
from pykantui.tui.status_styles import resolve_status_color, workflow_status_class
from pykantui.tui.type_styles import resolve_type_color, work_item_type_class
from pykantui.tui.widgets.work_item_table import DetailField, WorkItemTable
from pykantui.tui.widgets.work_items import WorkItemsView
from tests.integration.tui.test_board_tui import workflow_backend


class CompleteFieldsBackend(JsonBackend):
    """Deterministic local backend exposing every normalized table field."""

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        return frozenset(WorkItemColumn)


class ProviderFieldsBackend(CompleteFieldsBackend):
    """Backend fixture with an explicit provider capability contract."""

    def __init__(
        self,
        *,
        available: frozenset[WorkItemColumn],
        editable: frozenset[str],
    ) -> None:
        self._available = available
        self._editable = editable
        super().__init__()

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        return self._available

    def editable_task_fields(self) -> frozenset[str]:
        return self._editable


def complete_fields_backend() -> CompleteFieldsBackend:
    backend = CompleteFieldsBackend()
    backend.create_task(
        Task(
            task_id=1,
            title="A deliberately long provider summary that needs ellipsis",
            column_id=1,
            due_date=date(2026, 8, 30),
            metadata={
                "key": "JPT-1",
                "issue_type": "Story",
                "assignee": "Alexandra Example",
                "reporter": "Riley Reporter",
                "priority": "High",
                "labels": ["backend", "release"],
                "components": ["API", "Authentication"],
            },
        )
    )
    backend.create_task(Task(task_id=2, title="Empty provider fields", column_id=2))
    return backend


def header_offset(table: DataTable[object], column: WorkItemColumn) -> Offset:
    """Return a genuine click position near the center of one visible header."""
    left = 0
    for table_column in table.ordered_columns:
        width = table_column.get_render_width(table)
        if str(table_column.key.value) == column.value:
            return Offset(left + max(1, width // 2), 0)
        left += width
    raise AssertionError(f"{column.value} is not rendered")


def header_label(table: DataTable[object], column: WorkItemColumn) -> str:
    """Return one public-keyed column label without reaching into its key map."""
    return str(table.ordered_columns[table.get_column_index(column.value)].label)


def column_key(table: DataTable[object], column: WorkItemColumn) -> ColumnKey:
    """Return Textual's opaque key for one provider-neutral column."""
    return table.ordered_columns[table.get_column_index(column.value)].key


async def wait_for_class(pilot: Pilot[None], widget: Widget, class_name: str, *, timeout: float = 5.0) -> None:
    """Wait until a posted message lands as a class change; fail rather than hang.

    Setting ``Input.value`` posts ``Input.Changed`` to the message queue, and a
    bare ``pilot.pause()`` can return before the handler has run — the same race
    the ``settle`` helpers guard against elsewhere in this suite.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if widget.has_class(class_name):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{widget!r} never gained class {class_name!r} within {timeout}s")


class WorkItemTableInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rows_keyboard_open_routes_through_host_aware_launcher(self) -> None:
        backend = complete_fields_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/JPT-1"
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            with (
                patch.object(provider_links, "launch_external_url", create=True, return_value=True) as launcher,
            ):
                await pilot.press("ctrl+o")
                await pilot.pause()

        launcher.assert_called_once_with(app, "https://example.test/issues/JPT-1")

    async def test_rows_show_cached_provider_link_and_open_it_by_keyboard(self) -> None:
        backend = complete_fields_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/JPT-1"
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            root_screen = app.screen
            root_depth = len(app.screen_stack)
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.press("ctrl+o")
                await pilot.pause()

            key_cell = app.query_one(DataTable).get_cell("1", WorkItemColumn.KEY.value)
            screen_after = app.screen
            depth_after = len(app.screen_stack)

        self.assertIn("↗", str(key_cell))
        launcher.assert_called_once_with(app, "https://example.test/issues/JPT-1")
        self.assertIs(root_screen, screen_after)
        self.assertEqual(root_depth, depth_after)

    async def test_rows_only_show_the_selected_provider_arrow(self) -> None:
        backend = complete_fields_backend()
        for task_id in (1, 2):
            task = backend.get_task_by_id(task_id)
            assert task is not None
            task.metadata["url"] = f"https://example.test/issues/{task_id}"
            backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)

            self.assertIn("↗", str(table.get_cell("1", WorkItemColumn.KEY.value)))
            self.assertNotIn("↗", str(table.get_cell("2", WorkItemColumn.KEY.value)))

            table.move_cursor(row=1)
            await pilot.pause()

            self.assertNotIn("↗", str(table.get_cell("1", WorkItemColumn.KEY.value)))
            self.assertIn("↗", str(table.get_cell("2", WorkItemColumn.KEY.value)))
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.press("ctrl+o")
                await pilot.pause()

        launcher.assert_called_once_with(app, "https://example.test/issues/2")

    async def test_row_provider_arrow_click_requires_that_row_to_be_selected(self) -> None:
        backend = complete_fields_backend()
        for task_id in (1, 2):
            task = backend.get_task_by_id(task_id)
            assert task is not None
            task.metadata["url"] = f"https://example.test/issues/{task_id}"
            backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)
            key_x = header_offset(table, WorkItemColumn.KEY).x
            second_row_y = table.header_height + 1

            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.click("#work-items-table", offset=(key_x, second_row_y))
                await pilot.pause()
                launcher.assert_not_called()
                selected = app.query_one(WorkItemsView).selected_task()
                self.assertIsNotNone(selected)
                self.assertEqual(2, selected.task_id)  # type: ignore[union-attr]

                await pilot.click("#work-items-table", offset=(key_x, second_row_y))
                await pilot.pause()

        launcher.assert_called_once_with(app, "https://example.test/issues/2")

    async def test_opening_cached_link_performs_no_backend_lookup_or_refresh(self) -> None:
        backend = complete_fields_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/JPT-1"
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            with (
                patch.object(provider_links, "launch_external_url", return_value=True) as launcher,
                patch.object(backend, "get_task_by_id", wraps=backend.get_task_by_id) as task_lookup,
                patch.object(backend, "get_tasks", wraps=backend.get_tasks) as task_listing,
            ):
                view.action_open_provider()
                task_lookup.assert_not_called()
                task_listing.assert_not_called()

        launcher.assert_called_once_with(app, "https://example.test/issues/JPT-1")

    async def test_rows_without_a_safe_provider_url_show_and_open_nothing(self) -> None:
        backend = complete_fields_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "javascript:alert(1)"
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.press("ctrl+o")
                await pilot.pause()

            key_cell = app.query_one(DataTable).get_cell("1", WorkItemColumn.KEY.value)

        self.assertNotIn("↗", str(key_cell))
        launcher.assert_not_called()

    async def test_clicking_a_key_without_a_safe_url_still_selects_that_row(self) -> None:
        backend = complete_fields_backend()
        first = backend.get_task_by_id(1)
        assert first is not None
        first.metadata["url"] = "javascript:alert(1)"
        backend.update_task(first)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)
            table.move_cursor(row=1)
            await pilot.pause()
            await pilot.click("#work-items-table", offset=(24, 1))
            await pilot.pause()
            selected = app.query_one(WorkItemsView).selected_task()

        self.assertIsNotNone(selected)
        self.assertEqual(1, selected.task_id)  # type: ignore[union-attr]

    async def test_split_has_a_clickable_small_provider_arrow(self) -> None:
        backend = complete_fields_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/JPT-1"
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            app.query_one(WorkItemsView).action_focus_tab("info")
            await pilot.pause()
            root_screen = app.screen
            root_depth = len(app.screen_stack)
            link = app.query_one("#work-item-provider-link")
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.click("#work-item-provider-link")
                await pilot.pause()
            link_width = link.region.width
            screen_after = app.screen
            depth_after = len(app.screen_stack)

        self.assertEqual("↗", str(link.render()))
        self.assertEqual(1, link_width)
        launcher.assert_called_once_with(app, "https://example.test/issues/JPT-1")
        self.assertIs(root_screen, screen_after)
        self.assertEqual(root_depth, depth_after)

    async def test_a_stale_row_link_message_cannot_open_a_reused_task_id(self) -> None:
        backend = complete_fields_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata.update({"id": "old-provider-id", "url": "https://example.test/issues/old"})
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            replacement = task.model_copy(
                update={"metadata": {**task.metadata, "id": "new-provider-id", "url": "https://example.test/issues/new"}}
            )
            view._tasks["1"] = replacement
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                view.post_message(
                    WorkItemTable.LinkRequested(
                        "1",
                        "old-provider-id",
                        "https://example.test/issues/old",
                    )
                )
                await pilot.pause()

        launcher.assert_not_called()

    async def test_asana_contract_hides_type_from_rows_read_only_and_inline_editor(self) -> None:
        available = frozenset(
            {
                WorkItemColumn.SYNC,
                WorkItemColumn.NUMBER,
                WorkItemColumn.KEY,
                WorkItemColumn.STATUS,
                WorkItemColumn.SUMMARY,
                WorkItemColumn.ASSIGNEE,
                WorkItemColumn.REPORTER,
                WorkItemColumn.DUE,
                WorkItemColumn.CREATED,
            }
        )
        backend = ProviderFieldsBackend(
            available=available,
            editable=frozenset({"title", "body", "column_id", "assignee", "due_date"}),
        )
        backend.create_task(
            Task(
                task_id=1,
                title="Asana card",
                column_id=1,
                metadata={"issue_type": "Story", "assignee": "Alex", "reporter": "Riley"},
            )
        )
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            table = view.query_one(DataTable)

            self.assertNotIn(WorkItemColumn.TYPE.value, table.columns)
            self.assertFalse(view.query_one("#work-item-issue-type", DetailField).display)
            self.assertTrue(view.query_one("#work-item-assignee", DetailField).display)
            self.assertTrue(view.query_one("#work-item-reporter", DetailField).display)
            for selector in (
                "#work-item-priority",
                "#work-item-labels",
                "#work-item-components",
                "#work-item-parent",
                "#work-item-sprint",
                "#work-item-resolution",
                "#work-item-time-tracking",
            ):
                with self.subTest(selector=selector):
                    self.assertFalse(view.query_one(selector, DetailField).display)
            resolved = view.query_one("#work-item-resolved", DetailField)
            self.assertIsNotNone(resolved.parent)
            assert resolved.parent is not None
            self.assertFalse(resolved.parent.display)

            root_screen = app.screen
            await view.start_inline_edit()
            await pilot.pause()
            self.assertFalse(view.query("#work-item-edit-issue-type"))
            self.assertTrue(view.query("#work-item-edit-assignee"))
            self.assertFalse(view.query("#work-item-edit-priority"))
            self.assertFalse(view.query("#work-item-edit-labels"))
            self.assertFalse(view.query("#work-item-edit-components"))
            self.assertIs(root_screen, app.screen)

    async def test_jira_contract_shows_type_in_rows_read_only_and_inline_editor(self) -> None:
        backend = ProviderFieldsBackend(
            available=frozenset(WorkItemColumn),
            editable=frozenset(
                {
                    "title",
                    "body",
                    "column_id",
                    "assignee",
                    "issue_type",
                    "priority",
                    "labels",
                    "components",
                    "due_date",
                }
            ),
        )
        backend.create_task(
            Task(
                task_id=1,
                title="Jira card",
                column_id=1,
                metadata={"issue_type": "Bug", "assignee": "Alex", "reporter": "Riley"},
            )
        )
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            table = view.query_one(DataTable)

            self.assertIsNotNone(view.selected_task())
            self.assertIn(WorkItemColumn.TYPE.value, table.columns)
            self.assertTrue(view.query_one("#work-item-issue-type", DetailField).display)

            root_screen = app.screen
            await view.start_inline_edit()
            await pilot.pause()
            self.assertTrue(view.query("#work-item-edit-issue-type"))
            self.assertIs(root_screen, app.screen)

    async def test_real_header_click_sorts_and_switching_views_keeps_direction(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            table = view.query_one(DataTable)
            table.move_cursor(row=2)
            await pilot.pause()
            selected_id = view.selected_task().task_id  # type: ignore[union-attr]

            await pilot.click("#work-items-table", offset=header_offset(table, WorkItemColumn.STATUS))
            await pilot.pause()

            self.assertEqual(selected_id, view.selected_task().task_id)  # type: ignore[union-attr]
            self.assertEqual("Status ↑", header_label(table, WorkItemColumn.STATUS))

            await pilot.click("#work-items-table", offset=header_offset(table, WorkItemColumn.STATUS))
            await pilot.pause()
            self.assertEqual("Status ↓", header_label(table, WorkItemColumn.STATUS))

            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            self.assertEqual("Status ↓", header_label(table, WorkItemColumn.STATUS))

            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            await pilot.click("#work-items-table", offset=header_offset(table, WorkItemColumn.KEY))
            await pilot.pause()
            self.assertEqual("Key ↑", header_label(table, WorkItemColumn.KEY))
            self.assertEqual("Status", header_label(table, WorkItemColumn.STATUS))

    async def test_real_sync_and_number_header_clicks_never_change_sort(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)

            for column in (WorkItemColumn.SYNC, WorkItemColumn.NUMBER):
                await pilot.click("#work-items-table", offset=header_offset(table, column))
                await pilot.pause()

            self.assertFalse(app.view.sorted)
            self.assertNotIn("↑", " ".join(str(item.label) for item in table.columns.values()))

    async def test_every_provider_field_renders_with_bounded_width_and_safe_empty(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        app.view.columns = [*CORE_WORK_ITEM_COLUMNS, *OPTIONAL_WORK_ITEM_COLUMNS]

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)
            rendered = {
                WorkItemColumn(str(column.key.value)): column for column in table.ordered_columns
            }

            self.assertEqual(set(WorkItemColumn), set(rendered))
            for column in OPTIONAL_WORK_ITEM_COLUMNS:
                with self.subTest(column=column):
                    self.assertLessEqual(
                        rendered[column].get_render_width(table) - 2 * table.cell_padding,
                        WORK_ITEM_COLUMN_SPECS[column].preferred_width,
                    )
            self.assertEqual("—", str(table.get_cell("2", WorkItemColumn.ASSIGNEE.value)))
            self.assertEqual("—", str(table.get_cell("2", WorkItemColumn.REPORTER.value)))
            summary = table.get_cell("1", WorkItemColumn.SUMMARY.value)
            self.assertIsInstance(summary, Text)
            assert isinstance(summary, Text)
            self.assertEqual("ellipsis", summary.overflow)
            self.assertEqual(0, table.max_scroll_x)

    async def test_columns_action_updates_rows_but_cannot_remove_core_identity(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        # Start from the irreducible identity set so every optional column,
        # including the default-selected provider Type, exercises the same
        # add-then-remove path below.
        app.view.columns = list(CORE_WORK_ITEM_COLUMNS)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)

            for column in OPTIONAL_WORK_ITEM_COLUMNS:
                with self.subTest(column=column):
                    initially_visible = column.value in table.columns
                    app._run_view_action(Action.of(ActionKind.TABLE_COLUMN, column))
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(not initially_visible, column.value in table.columns)

                    app._run_view_action(Action.of(ActionKind.TABLE_COLUMN, column))
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(initially_visible, column.value in table.columns)

            app._run_view_action(Action.of(ActionKind.TABLE_COLUMN, WorkItemColumn.SUMMARY))
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn(WorkItemColumn.SUMMARY.value, table.columns)

    async def test_header_sort_and_column_toggle_never_call_backend_mutators(self) -> None:
        backend = complete_fields_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)
            with (
                patch.object(backend, "create_task", wraps=backend.create_task) as create,
                patch.object(backend, "update_task", wraps=backend.update_task) as update,
                patch.object(backend, "move_task", wraps=backend.move_task) as move,
                patch.object(backend, "delete_task", wraps=backend.delete_task) as delete,
            ):
                await pilot.click(
                    "#work-items-table",
                    offset=header_offset(table, WorkItemColumn.STATUS),
                )
                await pilot.pause()
                app._run_view_action(
                    Action.of(ActionKind.TABLE_COLUMN, WorkItemColumn.ASSIGNEE)
                )
                await app.workers.wait_for_complete()
                await pilot.pause()

            self.assertEqual((0, 0, 0, 0), (create.call_count, update.call_count, move.call_count, delete.call_count))

    async def test_wide_split_renders_people_columns_without_horizontal_scroll(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        app.view.columns = [
            *CORE_WORK_ITEM_COLUMNS,
            WorkItemColumn.ASSIGNEE,
            WorkItemColumn.REPORTER,
        ]

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            table = app.query_one(DataTable)

            self.assertIn(WorkItemColumn.ASSIGNEE.value, table.columns)
            self.assertIn(WorkItemColumn.REPORTER.value, table.columns)
            self.assertEqual(0, table.max_scroll_x)

    async def test_optional_choices_restore_after_narrow_layout_without_mutating_preferences(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        app.view.columns = [*CORE_WORK_ITEM_COLUMNS, *OPTIONAL_WORK_ITEM_COLUMNS]
        selected = list(app.view.columns)

        async with app.run_test(size=(80, 30)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            table = app.query_one(DataTable)
            self.assertEqual(0, table.max_scroll_x)
            self.assertTrue(
                {
                    WorkItemColumn.KEY.value,
                    WorkItemColumn.STATUS.value,
                    WorkItemColumn.SUMMARY.value,
                }.issubset(table.columns)
            )
            self.assertEqual(selected, app.view.columns)

            await pilot.resize_terminal(160, 40)
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            self.assertIn(WorkItemColumn.ASSIGNEE.value, table.columns)
            self.assertIn(WorkItemColumn.REPORTER.value, table.columns)
            self.assertEqual(selected, app.view.columns)
            self.assertEqual(0, table.max_scroll_x)

    async def test_live_resize_refits_rows_and_split_without_losing_selection(self) -> None:
        for layout in (BoardLayout.ROWS, BoardLayout.SPLIT):
            with self.subTest(layout=layout):
                app = KanbanApp(complete_fields_backend(), confirm_moves=False)
                app.view.columns = [*CORE_WORK_ITEM_COLUMNS, *OPTIONAL_WORK_ITEM_COLUMNS]
                selected_columns = list(app.view.columns)

                async with app.run_test(size=(160, 40)) as pilot:
                    app.set_board_layout(layout)
                    await pilot.pause()
                    view = app.query_one(WorkItemsView)
                    table = view.query_one(DataTable)
                    table.move_cursor(row=1)
                    await pilot.pause()
                    selected_id = view.selected_task().task_id  # type: ignore[union-attr]

                    await pilot.resize_terminal(80, 30)
                    await pilot.pause(0.12)
                    self.assertEqual(0, table.max_scroll_x)
                    self.assertEqual(selected_id, view.selected_task().task_id)  # type: ignore[union-attr]
                    self.assertEqual(selected_columns, app.view.columns)

                    await pilot.resize_terminal(160, 40)
                    await pilot.pause(0.12)
                    self.assertIn(WorkItemColumn.ASSIGNEE.value, table.columns)
                    self.assertEqual(selected_id, view.selected_task().task_id)  # type: ignore[union-attr]
                    self.assertEqual(selected_columns, app.view.columns)

    async def test_live_resize_does_not_rebuild_a_dirty_inline_editor(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        app.view.columns = [*CORE_WORK_ITEM_COLUMNS, *OPTIONAL_WORK_ITEM_COLUMNS]

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            await view.start_inline_edit()
            await pilot.pause()
            summary = view.query_one("#work-item-edit-summary", Input)
            summary.value = "keep this resize draft"
            status = view.query_one("#work-item-edit-status", Select)

            await pilot.resize_terminal(80, 30)
            await pilot.pause(0.12)

            self.assertTrue(view.editing)
            self.assertIs(summary, view.query_one("#work-item-edit-summary", Input))
            self.assertIs(status, view.query_one("#work-item-edit-status", Select))
            self.assertEqual("keep this resize draft", summary.value)

            await view.cancel_inline_edit()
            await pilot.pause()
            self.assertFalse(view.editing)
            self.assertEqual(0, view.query_one(DataTable).max_scroll_x)

    async def test_resize_burst_coalesces_to_one_table_rebuild(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        app.view.columns = [*CORE_WORK_ITEM_COLUMNS, *OPTIONAL_WORK_ITEM_COLUMNS]

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            size = Size(159, 40)

            with patch.object(view, "refresh_tasks", wraps=view.refresh_tasks) as refresh:
                for _ in range(20):
                    view.on_resize(Resize(size, size))
                await pilot.pause(0.12)

            self.assertEqual(1, refresh.call_count)

    async def test_split_divider_shrink_and_grow_refits_selected_columns(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        app.view.columns = [*CORE_WORK_ITEM_COLUMNS, *OPTIONAL_WORK_ITEM_COLUMNS]

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            table = view.query_one(DataTable)
            table.move_cursor(row=1)
            await pilot.pause()
            selected_id = view.selected_task().task_id  # type: ignore[union-attr]

            view._set_list_percent(view.MIN_LIST_PERCENT)
            await pilot.pause(0.12)
            self.assertEqual(0, table.max_scroll_x)
            self.assertNotIn(WorkItemColumn.ASSIGNEE.value, table.columns)

            view._set_list_percent(view.MAX_LIST_PERCENT)
            await pilot.pause(0.12)
            self.assertIn(WorkItemColumn.ASSIGNEE.value, table.columns)
            self.assertIn(WorkItemColumn.REPORTER.value, table.columns)
            self.assertEqual(selected_id, view.selected_task().task_id)  # type: ignore[union-attr]
            self.assertEqual(0, table.max_scroll_x)

    async def test_split_divider_change_defers_while_editor_is_dirty(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)
        app.view.columns = [*CORE_WORK_ITEM_COLUMNS, *OPTIONAL_WORK_ITEM_COLUMNS]

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            await view.start_inline_edit()
            await pilot.pause()
            summary = view.query_one("#work-item-edit-summary", Input)
            summary.value = "keep divider draft"

            view._set_list_percent(view.MIN_LIST_PERCENT)
            view._set_list_percent(view.MAX_LIST_PERCENT)
            await pilot.pause(0.12)

            self.assertTrue(view.editing)
            self.assertIs(summary, view.query_one("#work-item-edit-summary", Input))
            self.assertEqual("keep divider draft", summary.value)

    async def test_status_cells_and_split_fields_follow_semantics_in_every_theme(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            table = view.query_one(DataTable)
            themes = tuple(sorted(app.available_themes))

            for theme in themes:
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.pause()
                    task = view.selected_task()
                    assert task is not None
                    group = app.backend.column_group(task.column_id)
                    status = table.get_cell(str(task.task_id), WorkItemColumn.STATUS.value)
                    self.assertIsInstance(status, Text)
                    assert isinstance(status, Text)
                    assert isinstance(status.style, Style)
                    expected = resolve_status_color(group, app.theme_variables).rich_color
                    self.assertEqual(expected, status.style.color)

                    detail = view.query_one("#work-item-status", DetailField)
                    self.assertTrue(detail.has_class(workflow_status_class(group)))
                    self.assertEqual(expected, detail.styles.border.top[1].rich_color)

                    item_type = str(task.metadata.get("issue_type") or "")
                    type_cell = table.get_cell(str(task.task_id), WorkItemColumn.TYPE.value)
                    self.assertIsInstance(type_cell, Text)
                    assert isinstance(type_cell, Text)
                    assert isinstance(type_cell.style, Style)
                    type_color = resolve_type_color(item_type, app.theme_variables).rich_color
                    self.assertEqual(type_color, type_cell.style.color)

                    type_detail = view.query_one("#work-item-issue-type", DetailField)
                    self.assertTrue(type_detail.has_class(work_item_type_class(item_type)))
                    self.assertEqual(type_color, type_detail.styles.border.top[1].rich_color)

            self.assertIn("ansi-dark", themes)
            self.assertIn("ansi-light", themes)

    async def test_inline_type_uses_shared_semantics_and_never_opens_a_modal(self) -> None:
        app = KanbanApp(complete_fields_backend(), confirm_moves=False)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            root_screen = app.screen

            await view.start_inline_edit()
            await pilot.pause()
            item_type = view.query_one("#work-item-edit-issue-type", Input)
            self.assertTrue(item_type.has_class(work_item_type_class("Story")))
            self.assertEqual(
                resolve_type_color("Story", app.theme_variables).rich_color,
                item_type.styles.border.top[1].rich_color,
            )

            item_type.value = "Bug"
            await wait_for_class(pilot, item_type, work_item_type_class("Bug"))
            self.assertTrue(item_type.has_class(work_item_type_class("Bug")))
            self.assertEqual(
                resolve_type_color("Bug", app.theme_variables).rich_color,
                item_type.styles.border.top[1].rich_color,
            )
            self.assertIs(root_screen, app.screen)

    async def test_unknown_status_is_neutral_and_inline_selector_tracks_changes(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            selected = view.selected_task()
            assert selected is not None

            with patch.object(backend, "column_group", return_value=ColumnGroup.UNKNOWN):
                view.refresh_tasks()
                await pilot.pause()
                neutral = table_status(view, selected)
                assert isinstance(neutral.style, Style)
                expected = resolve_status_color(ColumnGroup.UNKNOWN, app.theme_variables).rich_color
                self.assertEqual(expected, neutral.style.color)
                self.assertTrue(
                    view.query_one("#work-item-status", DetailField).has_class(
                        "workflow-status-foreground"
                    )
                )

            view.refresh_tasks()
            await view.start_inline_edit()
            await pilot.pause()
            status_select = view.query_one("#work-item-edit-status", Select)
            current_group = backend.column_group(selected.column_id)
            self.assertTrue(status_select.has_class(workflow_status_class(current_group)))

            status_select.value = "2"
            await pilot.pause()
            self.assertTrue(status_select.has_class(workflow_status_class(backend.column_group(2))))

    async def test_dirty_inline_editor_ignores_header_sort_without_opening_a_modal(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(160, 40)) as pilot:
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            await view.start_inline_edit()
            await pilot.pause()
            summary = view.query_one("#work-item-edit-summary", Input)
            summary.value = "recover this draft"
            root_screen = app.screen
            table = view.query_one(DataTable)

            table.post_message(
                DataTable.HeaderSelected(
                    table,
                    column_key(table, WorkItemColumn.STATUS),
                    3,
                    Text("Status"),
                )
            )
            await pilot.pause()

            self.assertFalse(app.view.sorted)
            self.assertEqual("recover this draft", summary.value)
            self.assertIs(root_screen, app.screen)


def table_status(view: WorkItemsView, task: Task) -> Text:
    """Return one status cell with a precise type assertion for tests."""
    value = view.query_one(DataTable).get_cell(str(task.task_id), WorkItemColumn.STATUS.value)
    if not isinstance(value, Text):
        raise AssertionError(f"expected Rich Text status, got {type(value).__name__}")
    return value


if __name__ == "__main__":
    unittest.main()
