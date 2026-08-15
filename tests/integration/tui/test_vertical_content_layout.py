"""Vertical-space guarantees for Split and the card detail editor.

These journeys protect the layouts that are easiest to break when a provider
adds fields or the terminal becomes short: the main workspace must retain
usable height, long content must scroll vertically, and the actions must stay
reachable outside the scrolling content.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Button, Input, TabbedContent, TextArea

from pykantui.models import BoardLayout
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.edit import TaskEditScreen
from pykantui.tracker import get
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from tests.integration.tui.test_board_tui import workflow_backend

NORMAL_SIZE = (150, 40)
SHORT_SIZE = (120, 24)
COMPACT_POPUP_SIZE = (96, 18)
MIN_WRITING_HEIGHT = 8
MIN_NORMAL_EDITOR_HEIGHT = 10
MIN_WORKSPACE_HEIGHT = 10


async def settle(pilot: Pilot[None]) -> None:
    """Let Textual complete layout and any synchronous test workers."""
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


class VerticalContentLayoutTests(unittest.IsolatedAsyncioTestCase):
    """Content remains writable and reachable at normal and short heights."""

    async def test_normal_split_info_boxes_offer_real_writing_height(self) -> None:
        backend = workflow_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.description = "Provider description"
        task.metadata["private_notes"] = "Private planning notes"
        backend.update_task(task)

        with patch.object(backend, "supports_private_notes", return_value=True):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=NORMAL_SIZE) as pilot:
                await settle(pilot)
                app.set_board_layout(BoardLayout.SPLIT)
                app.query_one("#work-item-tabs", TabbedContent).active = "work-item-info-tab"
                await settle(pilot)

                description = app.query_one("#work-item-description")
                private_notes = app.query_one("#work-item-private-notes")
                info = app.query_one("#work-item-info-read", VerticalScroll)

                self.assertGreaterEqual(description.region.height, MIN_WRITING_HEIGHT)
                self.assertGreaterEqual(private_notes.region.height, MIN_WRITING_HEIGHT)
                self.assertEqual(0, info.max_scroll_x)
                self.assertEqual(1, info.styles.scrollbar_size_vertical)

    async def test_normal_split_editor_gives_both_markdown_fields_room_to_type(self) -> None:
        backend = workflow_backend()

        with patch.object(backend, "supports_private_notes", return_value=True):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=NORMAL_SIZE) as pilot:
                await settle(pilot)
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.press("e")
                await settle(pilot)

                description = app.query_one("#work-item-edit-description", TextArea)
                private_notes = app.query_one("#work-item-edit-private-notes", TextArea)
                info = app.query_one("#work-item-info-edit", VerticalScroll)

                self.assertGreaterEqual(description.region.height, MIN_NORMAL_EDITOR_HEIGHT)
                self.assertGreaterEqual(private_notes.region.height, MIN_NORMAL_EDITOR_HEIGHT)
                self.assertEqual(0, info.max_scroll_x)

    async def test_expanded_provider_filters_leave_a_usable_short_split_workspace(self) -> None:
        backend = workflow_backend()
        spec = get("jira").spec
        available = spec.available_table_fields({})
        editable = frozenset(spec.editable_card_fields({}))

        with (
            patch.object(backend, "supports_sync", True),
            patch.object(backend, "supports_query", True),
            patch.object(backend, "provider_filter_fields", return_value=spec.filter_fields({})),
            patch.object(backend, "available_task_fields", return_value=available),
            patch.object(backend, "editable_task_fields", return_value=editable),
        ):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=SHORT_SIZE) as pilot:
                await settle(pilot)
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.press("f2", "f2")
                await settle(pilot)

                workspace = app.query_one("#work-items-view", WorkItemsView)
                table = app.query_one("#work-items-table")
                sidebar = app.query_one("#work-item-detail-pane")
                detail_scroll = app.query_one("#work-item-detail-scroll", VerticalScroll)

                self.assertGreaterEqual(workspace.region.height, MIN_WORKSPACE_HEIGHT)
                self.assertGreaterEqual(table.region.height, MIN_WORKSPACE_HEIGHT - 3)
                self.assertGreaterEqual(sidebar.region.height, MIN_WORKSPACE_HEIGHT)
                self.assertTrue(detail_scroll.allow_vertical_scroll)
                self.assertEqual(0, detail_scroll.max_scroll_x)

    async def test_expanded_provider_filters_keep_a_minimum_board_at_eighteen_rows(self) -> None:
        backend = workflow_backend()
        spec = get("jira").spec

        with (
            patch.object(backend, "supports_sync", True),
            patch.object(backend, "supports_query", True),
            patch.object(backend, "provider_filter_fields", return_value=spec.filter_fields({})),
        ):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=(120, 18)) as pilot:
                await settle(pilot)
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.press("f2", "f2")
                await settle(pilot)

                panel = app.query_one("#bar-panel", VerticalScroll)
                workspace = app.query_one("#work-items-view", WorkItemsView)

                self.assertGreater(panel.max_scroll_y, 0)
                self.assertEqual(1, panel.styles.scrollbar_size_vertical)
                self.assertGreaterEqual(workspace.region.height, MIN_WORKSPACE_HEIGHT)

                last_control = app.query_one("#filter-state")
                last_control.focus()
                await pilot.pause()
                self.assertGreater(panel.scroll_y, 0)
                self.assertLessEqual(last_control.content_region.bottom, panel.content_region.bottom)

    async def test_normal_detail_popup_has_tall_description_and_private_notes(self) -> None:
        backend = workflow_backend()

        with patch.object(backend, "supports_private_notes", return_value=True):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=NORMAL_SIZE) as pilot:
                await settle(pilot)
                await pilot.press("e")
                await pilot.pause()

                self.assertIsInstance(app.screen, TaskDetailScreen)
                description = app.screen.query_one("#detail-notes", TextArea)
                private_notes = app.screen.query_one("#detail-private-notes", TextArea)
                body = app.screen.query_one("#detail-body", VerticalScroll)

                self.assertGreaterEqual(description.region.height, MIN_WRITING_HEIGHT)
                self.assertGreaterEqual(private_notes.region.height, MIN_WRITING_HEIGHT)
                self.assertEqual(0, body.max_scroll_x)

    async def test_read_only_popup_description_can_scroll_but_cannot_change(self) -> None:
        backend = workflow_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.description = "\n".join(f"Read-only line {number}" for number in range(30))
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()

            notes = app.screen.query_one("#detail-notes", TextArea)
            original = notes.text
            self.assertFalse(notes.disabled)
            self.assertTrue(notes.read_only)

            notes.focus()
            await pilot.press("pagedown")
            await pilot.pause()
            self.assertGreater(notes.scroll_y, 0)
            await pilot.press("x")
            self.assertEqual(original, notes.text)

    async def test_short_detail_popup_scrolls_to_private_notes_with_actions_pinned(self) -> None:
        backend = workflow_backend()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.description = "\n".join(f"Description line {number}" for number in range(12))
        task.metadata["private_notes"] = "\n".join(f"Private line {number}" for number in range(12))
        backend.update_task(task)

        with patch.object(backend, "supports_private_notes", return_value=True):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=COMPACT_POPUP_SIZE) as pilot:
                await settle(pilot)
                await pilot.press("e")
                await pilot.pause()

                body = app.screen.query_one("#detail-body", VerticalScroll)
                private_notes = app.screen.query_one("#detail-private-notes", TextArea)
                save = app.screen.query_one("#detail-primary", Button)
                close = app.screen.query_one("#detail-close", Button)

                self.assertTrue(body.allow_vertical_scroll)
                self.assertGreater(body.max_scroll_y, 0)
                self.assertEqual(0, body.max_scroll_x)
                self.assertEqual(1, body.styles.scrollbar_size_vertical)

                private_notes.focus()
                await pilot.pause()
                self.assertGreater(body.scroll_y, 0)
                self.assertTrue(private_notes.has_focus)
                # Textual reserves the bottom border row of the focused
                # TextArea at the viewport edge; no editable content is lost.
                self.assertLessEqual(private_notes.content_region.bottom, body.content_region.bottom)

                self.assertGreater(save.region.width, 0)
                self.assertGreater(close.region.width, 0)
                self.assertLessEqual(save.region.bottom, app.screen.region.bottom)
                self.assertLessEqual(close.region.bottom, app.screen.region.bottom)
                self.assertGreaterEqual(save.region.y, body.region.bottom)
                self.assertGreaterEqual(close.region.y, body.region.bottom)

                # Scrolling must not drop or replace the user's draft.
                summary = app.screen.query_one("#detail-summary", Input)
                summary.value = "Keep this short-terminal draft"
                private_notes.focus()
                await pilot.pause()
                self.assertEqual("Keep this short-terminal draft", summary.value)

    async def test_short_new_card_popup_scrolls_to_notes_with_actions_pinned(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=COMPACT_POPUP_SIZE) as pilot:
            await settle(pilot)
            await pilot.press("n")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskEditScreen)
            body = app.screen.query_one("#edit-body", VerticalScroll)
            notes = app.screen.query_one("#edit-notes", TextArea)
            save = app.screen.query_one("#edit-save", Button)
            cancel = app.screen.query_one("#edit-cancel", Button)

            self.assertTrue(body.allow_vertical_scroll)
            self.assertGreater(body.max_scroll_y, 0)
            self.assertEqual(0, body.max_scroll_x)
            self.assertEqual(1, body.styles.scrollbar_size_vertical)

            title = app.screen.query_one("#edit-title", Input)
            title.value = "Keep this new-card draft"
            notes.focus()
            await pilot.pause()
            self.assertGreater(body.scroll_y, 0)
            self.assertTrue(notes.has_focus)
            self.assertLessEqual(notes.region.bottom, body.content_region.bottom)

            self.assertGreater(save.region.width, 0)
            self.assertGreater(cancel.region.width, 0)
            self.assertLessEqual(save.region.bottom, app.screen.region.bottom)
            self.assertLessEqual(cancel.region.bottom, app.screen.region.bottom)
            self.assertGreaterEqual(save.region.y, body.region.bottom)
            self.assertGreaterEqual(cancel.region.y, body.region.bottom)
            self.assertEqual("Keep this new-card draft", title.value)


if __name__ == "__main__":
    unittest.main()
