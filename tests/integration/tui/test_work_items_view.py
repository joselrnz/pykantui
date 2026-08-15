"""Rows and split views keep Kanban as the application's home."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from rich.cells import cell_len
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, Label, Static, TabbedContent

from pykantui.core.actions import Action, ActionKind, Menu
from pykantui.core.filters import SortKey
from pykantui.core.workflows import TODO_COLUMN
from pykantui.models import BoardLayout, MoveResult, Task
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.grouped_palette import GroupedCommandPalette
from pykantui.pages.menu import ContextMenuScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.app_header import AppHeader
from pykantui.tui.widgets.board import KanbanBoard
from pykantui.tui.widgets.compact_footer import CompactFooter
from pykantui.tui.widgets.dropdowns import DateInput
from pykantui.tui.widgets.work_items import WorkItemsView
from tests.integration.tui.test_board_tui import workflow_backend
from tests.integration.tui.test_split_sidebar_layout import wait_for_layout


class WorkItemsViewTests(unittest.IsolatedAsyncioTestCase):
    def test_selected_terminal_glyphs_are_one_cell_wide(self) -> None:
        for glyph in "⌂⌘⌥⌦⌫⌧⎋⎈⎇▤▥▦○●◍◌×↑↓—…":
            with self.subTest(glyph=glyph):
                self.assertEqual(1, cell_len(glyph))

    async def test_home_is_the_kanban_board(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            app.action_home()
            await pilot.pause()

            self.assertEqual(BoardLayout.KANBAN, app.board_layout)
            self.assertTrue(app.query_one(KanbanBoard).display)
            self.assertFalse(app.query_one(WorkItemsView).display)

    async def test_rows_and_split_are_real_view_menu_choices(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            labels = [item.label.strip() for item in app._menu_items(Menu.VIEW)[:3]]

        self.assertTrue(labels[0].endswith("▥ Kanban"))
        self.assertTrue(labels[1].endswith("▦ Split"))
        self.assertTrue(labels[2].endswith("▤ Rows"))

    async def test_rows_show_the_sync_gutter_and_hide_the_detail_pane(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._run_view_action(Action.of(ActionKind.LAYOUT, BoardLayout.ROWS))
            await app.workers.wait_for_complete()
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            table = view.query_one(DataTable)
            headings = [str(column.label) for column in table.columns.values()]

            self.assertEqual("⎇", headings[0])
            self.assertFalse(view.detail_visible)
            self.assertEqual(len(app.visible_tasks()), table.row_count)

    async def test_clicking_a_sortable_header_toggles_and_keeps_selected_identity(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            table = view.query_one(DataTable)
            table.move_cursor(row=1)
            await pilot.pause()
            selected_id = view.selected_task().task_id  # type: ignore[union-attr]

            status_index = [str(column.label) for column in table.columns.values()].index("Status")
            table.post_message(
                DataTable.HeaderSelected(table, table.ordered_columns[status_index].key, status_index, Text("Status"))
            )
            await pilot.pause()

            self.assertEqual(selected_id, view.selected_task().task_id)  # type: ignore[union-attr]
            self.assertIn("Status ↑", [str(column.label) for column in table.columns.values()])

            status_index = [str(column.label) for column in table.columns.values()].index("Status ↑")
            table.post_message(
                DataTable.HeaderSelected(table, table.ordered_columns[status_index].key, status_index, Text("Status ↑"))
            )
            await pilot.pause()
            self.assertIn("Status ↓", [str(column.label) for column in table.columns.values()])

            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            self.assertIn("Status ↓", [str(column.label) for column in table.columns.values()])

    async def test_sync_and_row_number_headers_do_not_sort(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one(DataTable)
            before = [row.key.value for row in table.ordered_rows]

            for index in (0, 1):
                column = table.ordered_columns[index]
                table.post_message(DataTable.HeaderSelected(table, column.key, index, column.label))
                await pilot.pause()

            self.assertEqual(before, [row.key.value for row in table.ordered_rows])
            self.assertNotIn("↑", " ".join(str(column.label) for column in table.columns.values()))

    async def test_rows_never_need_horizontal_scrolling_at_common_widths(self) -> None:
        for width in (80, 160):
            with self.subTest(width=width):
                app = KanbanApp(workflow_backend(), confirm_moves=False)
                async with app.run_test(size=(width, 40)) as pilot:
                    await pilot.pause()
                    app.set_board_layout(BoardLayout.ROWS)
                    await pilot.pause()
                    self.assertEqual(0, app.query_one(DataTable).max_scroll_x)

    async def test_split_ellipsizes_long_summaries_without_horizontal_scrolling(self) -> None:
        backend = workflow_backend()
        first = backend.get_tasks()[0]
        backend.update_task(
            first.model_copy(
                update={
                    "title": (
                        "release-target Jira scale item with a deliberately long summary "
                        "that must stay inside the split list pane"
                    )
                }
            )
        )
        for task_id in range(8, 70):
            backend.create_task(
                Task(task_id=task_id, title=f"overflow row {task_id}", column_id=TODO_COLUMN)
            )
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            table = app.query_one(DataTable)
            summary = table.get_row_at(0)[-1]

            widths = [column.get_render_width(table) for column in table.columns.values()]
            self.assertEqual(
                0,
                table.max_scroll_x,
                (table.region.width, table.content_region.width, table.virtual_size.width, widths),
            )
            self.assertIsInstance(summary, Text)
            assert isinstance(summary, Text)
            self.assertEqual("ellipsis", summary.overflow)

    async def test_work_items_heading_tracks_rows_split_and_filtered_counts(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            heading = app.query_one("#work-items-heading", Label)
            self.assertEqual("Work Items (7)", str(heading.content))

            app.view.card_filter.text = "does-not-match-any-work-item"
            await app.apply_view()
            await pilot.pause()
            self.assertEqual("Work Items (0)", str(heading.content))

            app.view.card_filter.text = ""
            app.view.card_filter.column_id = TODO_COLUMN
            app.set_board_layout(BoardLayout.SPLIT)
            await app.apply_view()
            await pilot.pause()
            self.assertEqual("Work Items (3)", str(heading.content))

    async def test_work_items_heading_handles_large_visible_counts(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)
        large_tasks = [
            Task(task_id=index, title=f"task {index}", column_id=TODO_COLUMN)
            for index in range(1, 1_001)
        ]

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            view = app.query_one(WorkItemsView)

            with patch.object(app, "visible_tasks", return_value=large_tasks):
                view.refresh_tasks()
                # Keep the source stable through the compositor turn: layout
                # messages may legitimately request another refresh.
                await pilot.pause()

                self.assertEqual(1_000, view.query_one(DataTable).row_count)
                self.assertEqual(
                    "Work Items (1000)",
                    str(app.query_one("#work-items-heading", Label).content),
                )

    async def test_right_clicking_a_row_opens_its_compact_action_menu(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            await pilot.click("#work-items-table", offset=(8, 2), button=3)
            await pilot.pause()

            screen = app.screen
            self.assertIsInstance(screen, ContextMenuScreen)
            assert isinstance(screen, ContextMenuScreen)
            self.assertEqual(["View", "Edit"], [item.label for item in screen.items])
            selected = app.query_one(WorkItemsView).selected_task()
            self.assertIsNotNone(selected)
            self.assertEqual(2, selected.task_id)  # type: ignore[union-attr]

    async def test_row_menu_edit_uses_the_existing_split_sidebar(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            root_screen = app.screen
            root_stack_size = len(app.screen_stack)

            await pilot.click("#work-items-table", offset=(8, 2), button=3)
            await pilot.pause()
            await pilot.press("down", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertIs(app.screen, root_screen)
            self.assertEqual(root_stack_size, len(app.screen_stack))
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))
            self.assertTrue(view.editing)
            self.assertEqual("second", app.query_one("#work-item-edit-summary", Input).value)

    async def test_double_clicking_a_row_opens_the_detail_popup(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            await pilot.click("#work-items-table", offset=(8, 1), times=2)
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            assert isinstance(app.screen, TaskDetailScreen)
            self.assertFalse(app.screen.editing)
            self.assertEqual(1, app.screen.task_.task_id)
            await pilot.press("escape")

    async def test_double_clicking_a_split_row_explicitly_opens_the_detail_popup(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            await pilot.click("#work-items-table", offset=(8, 2), times=2)
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            assert isinstance(app.screen, TaskDetailScreen)
            self.assertFalse(app.screen.editing)
            self.assertEqual(2, app.screen.task_.task_id)
            await pilot.press("escape")

    async def test_triple_clicking_a_row_opens_only_one_detail_popup(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            root_screen = app.screen
            root_stack_size = len(app.screen_stack)

            await pilot.click("#work-items-table", offset=(8, 1), times=3)
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            self.assertEqual(root_stack_size + 1, len(app.screen_stack))
            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(app.screen, root_screen)
            self.assertEqual(root_stack_size, len(app.screen_stack))

    async def test_deleted_row_cannot_be_viewed_from_an_already_open_rows_menu(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            root_screen = app.screen

            await pilot.click("#work-items-table", offset=(8, 1), button=3)
            await pilot.pause()
            self.assertIsInstance(app.screen, ContextMenuScreen)
            self.assertTrue(backend.delete_task(1).ok)
            app.query_one(WorkItemsView).refresh_tasks()

            await pilot.press("enter")
            await pilot.pause()
            stale_popup_opened = isinstance(app.screen, TaskDetailScreen)
            if stale_popup_opened:
                await pilot.press("escape")
                await pilot.pause()

            self.assertFalse(stale_popup_opened)
            self.assertIs(app.screen, root_screen)

    async def test_deleted_row_cannot_edit_a_different_card_from_an_open_split_menu(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            root_screen = app.screen
            view = app.query_one(WorkItemsView)

            await pilot.click("#work-items-table", offset=(8, 1), button=3)
            await pilot.pause()
            self.assertIsInstance(app.screen, ContextMenuScreen)
            self.assertTrue(backend.delete_task(1).ok)
            view.refresh_tasks()
            self.assertEqual(2, view.selected_task().task_id)  # type: ignore[union-attr]

            await pilot.press("down", "enter")
            await pilot.pause()
            wrong_card_editing = view.editing
            if wrong_card_editing:
                await view.cancel_inline_edit()

            self.assertFalse(wrong_card_editing)
            self.assertIs(app.screen, root_screen)

    async def test_single_clicking_a_row_only_selects_it(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            root_screen = app.screen

            await pilot.click("#work-items-table", offset=(8, 2))
            await pilot.pause()

            self.assertIs(app.screen, root_screen)
            selected = app.query_one(WorkItemsView).selected_task()
            self.assertIsNotNone(selected)
            self.assertEqual(2, selected.task_id)  # type: ignore[union-attr]

    async def test_filtered_and_sorted_mouse_actions_preserve_exact_task_identity(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.view.card_filter.column_id = TODO_COLUMN
            app.view.sort = SortKey.TITLE
            app.view.reverse = True
            app.set_board_layout(BoardLayout.ROWS)
            await app.apply_view()
            await pilot.pause()

            self.assertEqual([3, 2, 1], [task.task_id for task in app.visible_tasks()])
            await pilot.click("#work-items-table", offset=(8, 1), button=3)
            await pilot.pause()
            selected = app.query_one(WorkItemsView).selected_task()
            self.assertIsNotNone(selected)
            self.assertEqual(3, selected.task_id)  # type: ignore[union-attr]
            await pilot.press("enter")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            assert isinstance(app.screen, TaskDetailScreen)
            self.assertEqual(3, app.screen.task_.task_id)
            await pilot.press("escape")

    async def test_rows_right_click_view_opens_read_only_popup_without_inline_editor(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            view = app.query_one(WorkItemsView)

            self.assertFalse(view.detail_visible)
            self.assertFalse(view.editing)
            self.assertEqual(0, len(view.query("#work-item-edit-summary")))

            await pilot.click("#work-items-table", offset=(8, 2), button=3)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            assert isinstance(app.screen, TaskDetailScreen)
            self.assertEqual(2, app.screen.task_.task_id)
            self.assertFalse(app.screen.editing)
            self.assertFalse(view.detail_visible)
            self.assertFalse(view.editing)
            self.assertEqual(0, len(view.query("#work-item-edit-summary")))
            await pilot.press("escape")

    async def test_right_clicking_a_scrolled_row_preserves_its_exact_task_identity(self) -> None:
        backend = JsonBackend()
        for task_id in range(1, 41):
            backend.create_task(Task(task_id=task_id, title=f"task {task_id:02d}", column_id=TODO_COLUMN))
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(100, 18)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            table = app.query_one("#work-items-table", DataTable)
            table.action_scroll_bottom()
            # The click coordinates below are derived from scroll_offset, so the
            # scroll must have actually landed first — one pause can return early.
            await wait_for_layout(
                pilot,
                lambda: table.scroll_offset.y >= table.max_scroll_y > 0,
                message="table never scrolled to bottom",
            )
            row_offset = table.header_height + table.cursor_coordinate.row - table.scroll_offset.y

            await pilot.click("#work-items-table", offset=(8, row_offset), button=3)
            await pilot.pause()

            self.assertIsInstance(app.screen, ContextMenuScreen)
            selected = app.query_one(WorkItemsView).selected_task()
            self.assertIsNotNone(selected)
            self.assertEqual(40, selected.task_id)  # type: ignore[union-attr]
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, TaskDetailScreen)
            assert isinstance(app.screen, TaskDetailScreen)
            self.assertEqual(40, app.screen.task_.task_id)
            await pilot.press("escape")

    async def test_rows_menu_edit_opens_the_existing_edit_popup(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            await pilot.click("#work-items-table", offset=(8, 2), button=3)
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            assert isinstance(app.screen, TaskDetailScreen)
            self.assertTrue(app.screen.editing)
            self.assertEqual(2, app.screen.task_.task_id)
            await pilot.press("escape")

    async def test_read_only_row_omits_edit_and_popup_stays_read_only(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        with patch.object(backend, "can_edit_task", return_value=False):
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app.set_board_layout(BoardLayout.ROWS)
                await pilot.pause()

                await pilot.click("#work-items-table", offset=(8, 1), button=3)
                await pilot.pause()
                menu = app.screen
                self.assertIsInstance(menu, ContextMenuScreen)
                assert isinstance(menu, ContextMenuScreen)
                self.assertEqual(["View"], [item.label for item in menu.items])
                await pilot.press("escape")
                await pilot.click("#work-items-table", offset=(8, 1), times=2)
                await pilot.pause()

                self.assertIsInstance(app.screen, TaskDetailScreen)
                assert isinstance(app.screen, TaskDetailScreen)
                self.assertFalse(app.screen.writable)
                await pilot.press("e")
                await pilot.pause()
                self.assertFalse(app.screen.editing)
                await pilot.press("escape")

    async def test_header_and_empty_table_space_do_not_open_a_row_menu(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            root_screen = app.screen

            await pilot.click("#work-items-table", offset=(8, 0), button=3)
            await pilot.pause()
            self.assertIs(app.screen, root_screen)
            await pilot.click("#work-items-table", offset=(8, 14), button=3)
            await pilot.pause()
            self.assertIs(app.screen, root_screen)

    async def test_empty_rows_view_actions_are_inert(self) -> None:
        backend = JsonBackend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            root_screen = app.screen
            root_stack_size = len(app.screen_stack)
            view = app.query_one(WorkItemsView)

            for key in ("e", "v", "enter", "comma"):
                await pilot.press(key)
                await pilot.pause()
                self.assertIs(app.screen, root_screen)
            await pilot.click("#work-items-table", offset=(8, 3), button=3)
            await pilot.click("#work-items-table", offset=(8, 3), times=2)
            await pilot.pause()

            self.assertEqual(0, view.query_one(DataTable).row_count)
            self.assertIsNone(view.selected_task())
            self.assertIs(app.screen, root_screen)
            self.assertEqual(root_stack_size, len(app.screen_stack))

    async def test_filtering_to_zero_rows_clears_stale_split_sidebar_content(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            self.assertEqual("first", str(app.query_one("#work-item-info-summary", Static).content))

            app.view.card_filter.text = "does-not-match-any-work-item"
            await app.apply_view()
            await pilot.pause()

            self.assertEqual(0, view.query_one(DataTable).row_count)
            self.assertIsNone(view.selected_task())
            self.assertTrue(app.query_one("#work-item-edit-start", Button).disabled)
            self.assertEqual("—", str(app.query_one("#work-item-info-summary", Static).content))
            self.assertEqual("—", str(app.query_one("#work-item-description", Static).content))
            self.assertEqual("—", str(app.query_one("#work-item-private-notes", Static).content))

    async def test_comma_opens_the_same_row_menu_without_a_mouse(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            await pilot.press("comma")
            await pilot.pause()

            self.assertIsInstance(app.screen, ContextMenuScreen)
            assert isinstance(app.screen, ContextMenuScreen)
            self.assertEqual(["View", "Edit"], [item.label for item in app.screen.items])
            await pilot.press("escape")

    async def test_clicking_outside_the_row_menu_dismisses_without_editing(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            root_screen = app.screen

            with patch.object(backend, "update_task", wraps=backend.update_task) as update_task:
                await pilot.click("#work-items-table", offset=(8, 1), button=3)
                await pilot.pause()
                self.assertIsInstance(app.screen, ContextMenuScreen)
                await pilot.click(offset=(120, 30))
                await pilot.pause()

            self.assertIs(app.screen, root_screen)
            self.assertEqual(0, update_task.call_count)

    async def test_row_menu_is_clamped_inside_a_narrow_terminal(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            await pilot.click("#work-items-table", offset=(30, 7), button=3)
            await pilot.pause()

            self.assertIsInstance(app.screen, ContextMenuScreen)
            dialog = app.screen.query_one("#menu-dialog")
            self.assertGreaterEqual(dialog.region.x, 0)
            self.assertGreaterEqual(dialog.region.y, 0)
            self.assertLessEqual(dialog.region.right, app.size.width)
            self.assertLessEqual(dialog.region.bottom, app.size.height)
            await pilot.press("escape")

    async def test_split_shows_provider_details_and_home_returns_to_kanban(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertTrue(view.detail_visible)
            tabs = view.query_one("#work-item-tabs", TabbedContent)
            self.assertEqual("work-item-details-tab", tabs.active)

            await view.action_home()
            await pilot.pause()

        self.assertEqual(BoardLayout.KANBAN, app.board_layout)

    async def test_split_edit_stays_inside_the_sidebar_without_pushing_a_screen(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            original_screen = app.screen
            original_stack_size = len(app.screen_stack)

            await pilot.click("#work-item-edit-start")
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertIs(app.screen, original_screen)
            self.assertEqual(original_stack_size, len(app.screen_stack))
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))
            self.assertTrue(view.editing)
            self.assertFalse(app.query_one("#work-item-edit-summary", Input).disabled)
            self.assertTrue(app.query_one("#work-item-edit-save", Button).display)
            self.assertTrue(app.query_one("#work-item-edit-cancel", Button).display)

    async def test_concurrent_inline_edit_requests_mount_only_one_editor(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)

            await asyncio.gather(view.start_inline_edit(), view.start_inline_edit())
            await pilot.pause()

            self.assertTrue(view.editing)
            self.assertEqual(1, len(view.query("#work-item-edit-summary")))
            self.assertEqual(1, len(view.query("#work-item-edit-description")))
            self.assertEqual(1, len(view.query("#work-item-edit-due")))

    async def test_dirty_inline_editor_ignores_mouse_actions_on_other_rows(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            root_screen = app.screen
            root_stack_size = len(app.screen_stack)
            view = app.query_one(WorkItemsView)
            summary = app.query_one("#work-item-edit-summary", Input)
            summary.value = "keep this exact draft"
            selected = view.selected_task()
            self.assertIsNotNone(selected)
            selected_id = selected.task_id  # type: ignore[union-attr]

            await pilot.click("#work-items-table", offset=(8, 2), button=3)
            await pilot.click("#work-items-table", offset=(8, 2), times=2)
            await pilot.pause()

            self.assertTrue(view.editing)
            self.assertEqual(selected_id, view.selected_task().task_id)  # type: ignore[union-attr]
            self.assertEqual("keep this exact draft", summary.value)
            self.assertIs(app.screen, root_screen)
            self.assertEqual(root_stack_size, len(app.screen_stack))
            self.assertEqual(0, len(app.screen.query("#menu-dialog, #detail-dialog")))

    async def test_escape_cancels_sidebar_changes_and_stays_in_split(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            app.query_one("#work-item-edit-summary", Input).value = "discard this"

            await pilot.press("escape")
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertFalse(view.editing)
            self.assertEqual(BoardLayout.SPLIT, app.board_layout)
            self.assertEqual("first", backend.get_task_by_id(1).title)  # type: ignore[union-attr]

    async def test_ctrl_s_saves_sidebar_changes_without_opening_a_dialog(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            original_screen = app.screen
            await pilot.press("e")
            await pilot.pause()
            app.query_one("#work-item-edit-summary", Input).value = "edited in sidebar"

            await pilot.press("ctrl+s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertIs(app.screen, original_screen)
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))
            self.assertFalse(view.editing)
            self.assertEqual(BoardLayout.SPLIT, app.board_layout)
            self.assertEqual("edited in sidebar", backend.get_task_by_id(1).title)  # type: ignore[union-attr]

    async def test_blank_sidebar_summary_keeps_the_editor_open(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            summary = app.query_one("#work-item-edit-summary", Input)
            summary.value = "   "

            await pilot.press("ctrl+s")
            await pilot.pause()

            self.assertTrue(app.query_one(WorkItemsView).editing)
            self.assertTrue(summary.has_focus)
            self.assertEqual("first", backend.get_task_by_id(1).title)  # type: ignore[union-attr]

    async def test_invalid_sidebar_due_date_keeps_the_editor_open(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            due = app.query_one("#work-item-edit-due", DateInput)
            due.value = "next Tuesday"

            await pilot.press("ctrl+s")
            await pilot.pause()

            self.assertTrue(app.query_one(WorkItemsView).editing)
            self.assertTrue(due.has_focus)
            self.assertIsNone(backend.get_task_by_id(1).due_date)  # type: ignore[union-attr]

    async def test_sidebar_save_failure_preserves_the_recoverable_draft(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            summary = app.query_one("#work-item-edit-summary", Input)
            summary.value = "keep this draft"

            with patch.object(backend, "update_task", return_value=MoveResult.failure("disk unavailable")):
                await pilot.press("ctrl+s")
                await pilot.pause()

            self.assertTrue(app.query_one(WorkItemsView).editing)
            self.assertEqual("keep this draft", summary.value)
            self.assertFalse(app.query_one("#work-item-edit-save", Button).disabled)

    async def test_repeated_sidebar_save_submits_only_once(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            app.query_one("#work-item-edit-summary", Input).value = "one local save"
            view = app.query_one(WorkItemsView)

            with patch.object(backend, "update_task", wraps=backend.update_task) as update_task:
                await view.action_save_inline()
                await view.action_save_inline()
                for _ in range(10):
                    await pilot.pause()
                    if not view.editing:
                        break

            self.assertEqual(1, update_task.call_count)
            self.assertEqual("one local save", backend.get_task_by_id(1).title)  # type: ignore[union-attr]

    async def test_narrow_split_keeps_sidebar_save_and_cancel_visible(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()

            save = app.query_one("#work-item-edit-save", Button)
            cancel = app.query_one("#work-item-edit-cancel", Button)
            self.assertTrue(save.display)
            self.assertTrue(cancel.display)
            self.assertGreater(save.region.width, 0)
            self.assertGreater(cancel.region.width, 0)

    async def test_split_view_details_key_does_not_open_a_redundant_dialog(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            original_screen = app.screen

            await pilot.press("v")
            await pilot.pause()

            self.assertIs(app.screen, original_screen)
            self.assertEqual(BoardLayout.SPLIT, app.board_layout)
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))

    async def test_dirty_sidebar_blocks_layout_changes_until_save_or_cancel(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            app.query_one("#work-item-edit-summary", Input).value = "unsaved draft"

            app.set_board_layout(BoardLayout.KANBAN)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertTrue(view.editing)
            self.assertEqual(BoardLayout.SPLIT, app.board_layout)
            self.assertTrue(view.query_one(DataTable).disabled)
            self.assertEqual("unsaved draft", app.query_one("#work-item-edit-summary", Input).value)

    async def test_dirty_split_sidebar_blocks_switch_specifically_to_rows(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            draft = app.query_one("#work-item-edit-summary", Input)
            draft.value = "keep this split draft"

            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertEqual(BoardLayout.SPLIT, app.board_layout)
            self.assertTrue(view.detail_visible)
            self.assertTrue(view.editing)
            self.assertEqual("keep this split draft", draft.value)

    async def test_cancel_split_editor_then_rows_edit_uses_popup_only(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            self.assertTrue(view.editing)

            await view.cancel_inline_edit()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            self.assertEqual(BoardLayout.ROWS, app.board_layout)
            self.assertFalse(view.detail_visible)
            self.assertFalse(view.editing)
            self.assertEqual(0, len(view.query("#work-item-edit-summary")))
            self.assertEqual(0, len(view.query_one("#work-item-info-edit").children))
            self.assertEqual(0, len(view.query_one("#work-item-detail-edit").children))

            await pilot.click("#work-items-table", offset=(8, 2), button=3)
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            assert isinstance(app.screen, TaskDetailScreen)
            self.assertEqual(2, app.screen.task_.task_id)
            self.assertTrue(app.screen.editing)
            self.assertFalse(view.editing)
            self.assertEqual(0, len(view.query("#work-item-edit-summary")))
            await pilot.press("escape")

    async def test_rows_switch_during_inline_mount_cannot_leave_a_hidden_editor(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            info_host = view.query_one("#work-item-info-edit")
            original_mount = info_host.mount
            mount_started = asyncio.Event()
            release_mount = asyncio.Event()

            async def delayed_mount(
                *widgets: Widget,
                before: int | str | Widget | None = None,
                after: int | str | Widget | None = None,
            ) -> None:
                mount_started.set()
                await release_mount.wait()
                await original_mount(*widgets, before=before, after=after)

            with patch.object(info_host, "mount", new=delayed_mount):
                opening = asyncio.create_task(view.start_inline_edit())
                await asyncio.wait_for(mount_started.wait(), timeout=2)
                app.set_board_layout(BoardLayout.ROWS)
                release_mount.set()
                await asyncio.wait_for(opening, timeout=2)
                await pilot.pause()

            self.assertEqual(BoardLayout.SPLIT, app.board_layout)
            self.assertTrue(view.detail_visible)
            self.assertTrue(view.editing)
            self.assertEqual(1, len(view.query("#work-item-edit-summary")))

    async def test_dirty_sidebar_blocks_reload_until_save_or_cancel(self) -> None:
        backend = workflow_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            app.query_one("#work-item-edit-summary", Input).value = "recoverable draft"

            with patch.object(backend, "reload_local", wraps=backend.reload_local) as reload_local:
                await app.action_refresh_board()
            await pilot.pause()

            self.assertEqual(0, reload_local.call_count)
            self.assertTrue(app.query_one(WorkItemsView).editing)
            self.assertEqual("recoverable draft", app.query_one("#work-item-edit-summary", Input).value)

    async def test_dirty_sidebar_blocks_keyboard_and_header_exit(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            summary = app.query_one("#work-item-edit-summary", Input)
            summary.value = "do not lose this draft"

            await pilot.press("ctrl+q")
            await pilot.pause()

            self.assertTrue(app.is_running)
            self.assertTrue(app.query_one(WorkItemsView).editing)
            self.assertEqual("do not lose this draft", summary.value)

            await pilot.click("#app-header-exit")
            await pilot.pause()

            self.assertTrue(app.is_running)
            self.assertTrue(app.query_one(WorkItemsView).editing)
            self.assertEqual("do not lose this draft", summary.value)

    async def test_toolbar_uses_the_approved_terminal_actions(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.menu_bar.level = app.menu_bar.level.TOOLBAR
            await pilot.pause()

            home = str(app.menu_bar.query_one("#bar-home", Label).content)
            menu = str(app.query_one("#app-header-menu", Label).content)
            view = str(app.menu_bar.query_one("#bar-menu-view", Label).content)
            exit_button = str(app.query_one("#app-header-exit", Label).content)
            duplicate_menu = bool(app.menu_bar.query("#bar-menu"))
            has_close = bool(app.menu_bar.query("#bar-close"))

        self.assertEqual("⌂ Home", home)
        self.assertEqual("⌘ Menu", menu)
        self.assertEqual("▥ View", view)
        self.assertEqual("×", exit_button)
        self.assertFalse(duplicate_menu)
        self.assertFalse(has_close)

    async def test_header_menu_opens_the_searchable_grouped_palette(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#app-header-menu")
            await pilot.pause()

            palette = app.screen

        self.assertIsInstance(palette, GroupedCommandPalette)

    async def test_header_keeps_provider_identity_out_of_the_filter_bar(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            header = app.query_one(AppHeader)
            filter_bar = app.menu_bar

            provider = str(header.query_one("#app-header-provider", Label).content)
            has_sync = bool(header.query("#app-header-sync"))
            duplicate_provider = bool(filter_bar.query("#bar-provider"))
            duplicate_sync = bool(filter_bar.query("#bar-sync, #chip-act-sync"))

        self.assertEqual("⎈ json", provider)
        self.assertFalse(has_sync)
        self.assertFalse(duplicate_provider)
        self.assertFalse(duplicate_sync)

    async def test_compact_footer_shows_only_four_card_context_groups(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            footer = app.query_one(CompactFooter)
            footer.refresh_context()
            hints = footer.visible_hints

        self.assertEqual(
            (("H/L", "Move"), ("e", "Edit"), ("v", "Details"), (",", "Column")),
            hints,
        )

    async def test_compact_footer_changes_for_split_view(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            footer = app.query_one(CompactFooter)
            footer.refresh_context()
            hints = footer.visible_hints

        self.assertEqual((("e", "Edit"), ("v", "Details"), ("Esc", "Kanban")), hints)

    async def test_compact_footer_exposes_and_cancels_a_pending_move(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            board = app.query_one(KanbanBoard)
            board.target_column = app.visible_columns[1].column_id
            await pilot.pause()
            hints = app.query_one(CompactFooter).visible_hints

            await pilot.press("escape")
            await pilot.pause()
            target = board.target_column

        self.assertEqual(
            (("H/L", "Choose column"), ("Enter", "Move"), ("Esc", "Cancel")),
            hints,
        )
        self.assertIsNone(target)

    async def test_palette_view_command_runs_the_existing_layout_action(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            command = next(
                command for command in app.get_system_commands(app.screen) if command.title == "View · ▦ Split"
            )
            command.callback()
            await app.workers.wait_for_complete()
            await pilot.pause()

            layout = app.board_layout

        self.assertEqual(BoardLayout.SPLIT, layout)

    def test_header_menu_label_is_width_safe(self) -> None:
        self.assertLessEqual(cell_len("⌘ Menu"), 8)

    async def test_global_header_stays_inside_a_narrow_terminal(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            header = app.query_one(AppHeader)
            children = list(header.children)
            right_edge = max(child.region.right for child in children)
            menu_width = header.query_one("#app-header-menu", Label).region.width
            exit_width = header.query_one("#app-header-exit", Label).region.width

        self.assertLessEqual(right_edge, 80)
        self.assertEqual(8, menu_width)
        self.assertEqual(3, exit_width)

    async def test_view_menu_icon_follows_the_selected_layout(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            view = str(app.menu_bar.query_one("#bar-menu-view", Label).content)

        self.assertEqual("▦ View", view)

    async def test_jiratui_number_keys_focus_rows_and_select_detail_tabs(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            tabs = view.query_one("#work-item-tabs", TabbedContent)
            view.action_focus_tab("comments")
            await pilot.pause()
            self.assertEqual("work-item-comments-tab", tabs.active)

            view.action_focus_table()
            await pilot.pause()
            self.assertTrue(view.query_one(DataTable).has_focus)

    async def test_number_keys_switch_sidebar_tabs_while_editing(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            tabs = view.query_one("#work-item-tabs", TabbedContent)
            self.assertEqual("work-item-info-tab", tabs.active)

            view.action_focus_tab("details")
            await pilot.pause()

            self.assertEqual("work-item-details-tab", tabs.active)

    async def test_split_divider_can_resize_and_reset_with_keys(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            self.assertEqual(view.default_list_percent, view.list_percent)

            await pilot.press("right_square_bracket")
            self.assertEqual(view.default_list_percent + view.RESIZE_STEP, view.list_percent)

            await pilot.press("left_square_bracket")
            self.assertEqual(view.default_list_percent, view.list_percent)

            await pilot.press("right_square_bracket", "right_square_bracket")
            await pilot.press("backslash")
            self.assertEqual(view.default_list_percent, view.list_percent)

    async def test_split_divider_keeps_both_panes_usable(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            for _ in range(20):
                view.action_shrink_list()
            self.assertEqual(25, view.list_percent)

            for _ in range(20):
                view.action_grow_list()
            self.assertEqual(75, view.list_percent)

    async def test_split_has_a_visible_mouse_resize_handle(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            handle = app.query_one("#work-item-resizer", Static)

            self.assertTrue(handle.display)
            self.assertEqual("", str(handle.content))
            self.assertEqual(1, handle.size.width)
            self.assertEqual(0.0, handle.styles.background.a)

    async def test_dragging_the_split_handle_resizes_the_work_items_pane(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            handle = view.query_one("#work-item-resizer", Static)
            target_x = view.content_region.x + round(view.content_region.width * 0.60)
            target_y = handle.region.y + handle.region.height // 2

            await pilot.mouse_down(handle, offset=(0, handle.region.height // 2))
            await pilot.pause()
            self.assertTrue(handle.has_class("-dragging"))
            self.assertEqual(1.0, handle.styles.background.a)
            await pilot.hover(offset=(target_x, target_y))
            await pilot.mouse_up(offset=(target_x, target_y))
            await pilot.pause()

            self.assertGreaterEqual(view.list_percent, 58)
            self.assertLessEqual(view.list_percent, 62)
            self.assertFalse(app.mouse_captured)
            self.assertFalse(handle.has_class("-dragging"))

    async def test_view_menu_exposes_split_width_controls_only_in_split(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            kanban_labels = [item.label.strip() for item in app._menu_items(Menu.VIEW)]

            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            split_labels = [item.label.strip() for item in app._menu_items(Menu.VIEW)]

        self.assertFalse(any("work items" in label.lower() for label in kanban_labels))
        self.assertIn("[ Narrow work items", split_labels)
        self.assertIn("] Widen work items", split_labels)
        self.assertIn("\\ Reset divider", split_labels)
