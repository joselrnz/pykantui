"""Responsive layout guarantees for Split's read-only and editing sidebar."""

from __future__ import annotations

import asyncio
import time
import unittest
from collections.abc import Callable
from unittest.mock import patch

from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Button, Input, TabbedContent, TextArea

from pykantui.core.work_items import WorkItemColumn
from pykantui.models import BoardLayout
from pykantui.tracker.registry import get, specs
from pykantui.tracker.spec import ProviderSpec
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from tests.integration.tui.test_board_tui import workflow_backend


async def wait_for_layout(
    pilot: Pilot[None],
    condition: Callable[[], bool],
    *,
    timeout: float = 5.0,
    message: str = "layout never settled",
) -> None:
    """Drain until a layout condition holds; fail rather than hang.

    Scrollbars appearing, ``scroll_visible`` calls, and pane resizes all land
    a frame or more after the event that caused them, so a single pause can
    assert in between — the same race the suite's ``settle`` helpers guard.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{message} after {timeout}s")


async def wait_for_no_horizontal_overflow(
    pilot: Pilot[None], scroll: VerticalScroll, *, timeout: float = 5.0
) -> None:
    """Drain until the scroller stops reporting phantom horizontal overflow."""
    await wait_for_layout(
        pilot,
        lambda: scroll.max_scroll_x == 0,
        timeout=timeout,
        message=f"{scroll!r} still overflows horizontally",
    )


def provider_capabilities(
    name: str,
    *,
    configured: bool = False,
) -> tuple[frozenset[WorkItemColumn], frozenset[str]]:
    """Return a built-in provider's static field contract without networking."""
    spec = get(name).spec
    config = configured_provider_config(spec) if configured else {}
    return spec.available_table_fields(config), frozenset(spec.editable_card_fields(config))


def configured_provider_config(spec: ProviderSpec) -> dict[str, object]:
    """Populate every static configuration key so optional fields are visible."""
    return {field.name: "configured" for field in spec.config_fields}


class SplitSidebarLayoutTests(unittest.IsolatedAsyncioTestCase):
    """The Split sidebar remains useful at ordinary and short terminal sizes."""

    async def test_default_split_gives_the_editor_about_two_thirds_and_keeps_a_thin_divider(self) -> None:
        backend = workflow_backend()
        visible, editable = provider_capabilities("jira")

        with (
            patch.object(backend, "available_task_fields", return_value=visible),
            patch.object(backend, "editable_task_fields", return_value=editable),
            patch.object(backend, "supports_sync", True),
        ):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=(150, 40)) as pilot:
                await pilot.pause()
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.pause()

                view = app.query_one(WorkItemsView)
                left = app.query_one("#work-items-list-pane")
                divider = app.query_one("#work-item-resizer")
                right = app.query_one("#work-item-detail-pane")

                self.assertEqual(35, view.list_percent)
                self.assertEqual(1, divider.region.width)
                self.assertGreaterEqual(right.region.width, round((left.region.width + right.region.width) * 0.62))

    async def test_default_split_width_tracks_provider_field_density(self) -> None:
        cases = (
            ("asana", 45),
            ("github", 40),
            ("jira", 35),
        )

        for provider_name, expected_list_percent in cases:
            with self.subTest(provider=provider_name):
                backend = workflow_backend()
                visible, editable = provider_capabilities(provider_name)
                with (
                    patch.object(backend, "available_task_fields", return_value=visible),
                    patch.object(backend, "editable_task_fields", return_value=editable),
                    patch.object(backend, "supports_sync", True),
                ):
                    app = KanbanApp(backend, confirm_moves=False)
                    async with app.run_test(size=(150, 40)) as pilot:
                        await pilot.pause()
                        app.set_board_layout(BoardLayout.SPLIT)
                        await pilot.pause()

                        view = app.query_one(WorkItemsView)
                        self.assertEqual(expected_list_percent, view.list_percent)
                        self.assertEqual(expected_list_percent, view.default_list_percent)

    def test_all_builtin_provider_specs_have_an_explicit_density_tier(self) -> None:
        expected = {
            "asana": 45,
            "clickup": 35,
            "github": 40,
            "jira": 35,
            "linear": 40,
            "monday": 35,
            "plane": 40,
            "shortcut": 40,
            "trello": 45,
        }
        actual: dict[str, int] = {}
        for spec in specs(available_only=False):
            config = configured_provider_config(spec)
            actual[spec.name] = WorkItemsView._default_percent_for_field_density(
                spec.available_table_fields(config),
                spec.editable_card_fields(config),
                provider_backed=True,
            )

        self.assertEqual(expected, actual)

        monday = get("monday").spec
        self.assertEqual(
            45,
            WorkItemsView._default_percent_for_field_density(
                monday.available_table_fields({}),
                monday.editable_card_fields({}),
                provider_backed=True,
            ),
        )

    async def test_real_local_json_board_keeps_more_list_space_than_jira(self) -> None:
        local_app = KanbanApp(workflow_backend(), confirm_moves=False)
        async with local_app.run_test(size=(150, 40)) as pilot:
            await pilot.pause()
            local_app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            local_view = local_app.query_one(WorkItemsView)
            local_list_width = local_app.query_one("#work-items-list-pane").region.width
            self.assertEqual(45, local_view.list_percent)

        jira_backend = workflow_backend()
        visible, editable = provider_capabilities("jira")
        with (
            patch.object(jira_backend, "available_task_fields", return_value=visible),
            patch.object(jira_backend, "editable_task_fields", return_value=editable),
            patch.object(jira_backend, "supports_sync", True),
        ):
            jira_app = KanbanApp(jira_backend, confirm_moves=False)
            async with jira_app.run_test(size=(150, 40)) as pilot:
                await pilot.pause()
                jira_app.set_board_layout(BoardLayout.SPLIT)
                await pilot.pause()
                jira_view = jira_app.query_one(WorkItemsView)
                jira_list_width = jira_app.query_one("#work-items-list-pane").region.width
                self.assertEqual(35, jira_view.list_percent)

        self.assertGreater(local_list_width, jira_list_width)

    async def test_user_split_width_survives_capability_refresh_resize_view_roundtrip_and_draft(self) -> None:
        backend = workflow_backend()
        visible, editable = provider_capabilities("asana")

        with (
            patch.object(backend, "available_task_fields", return_value=visible) as available_fields,
            patch.object(backend, "editable_task_fields", return_value=editable) as editable_fields,
            patch.object(backend, "supports_sync", True),
        ):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=(150, 40)) as pilot:
                await pilot.pause()
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.pause()
                view = app.query_one(WorkItemsView)
                self.assertEqual(45, view.list_percent)

                view.action_grow_list()
                self.assertEqual(50, view.list_percent)

                # A later provider refresh may expose more fields, but it must
                # not override the ratio the user already chose.
                available_fields.return_value, editable_fields.return_value = provider_capabilities("jira")
                await pilot.resize_terminal(80, 24)
                await pilot.pause()
                self.assertEqual(50, view.list_percent)
                self.assertGreaterEqual(app.query_one("#work-item-detail-pane").region.width, 39)

                await pilot.resize_terminal(150, 40)
                app.set_board_layout(BoardLayout.ROWS)
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.pause()
                self.assertEqual(50, view.list_percent)

                view.action_reset_split()
                self.assertEqual(45, view.list_percent)

                await pilot.press("e")
                await pilot.pause()
                summary = app.query_one("#work-item-edit-summary", Input)
                summary.value = "keep the provider-aware draft"
                await pilot.resize_terminal(96, 18)
                await pilot.pause()

                self.assertEqual(45, view.list_percent)
                self.assertEqual("keep the provider-aware draft", summary.value)
                self.assertTrue(view.editing)

    async def test_short_read_only_info_is_vertically_scrollable_without_horizontal_overflow(self) -> None:
        backend = workflow_backend()
        task = backend.get_task_by_id(1)
        if task is None:
            self.fail("fixture task is missing")
        task.description = "\n".join(f"Provider description line {number}" for number in range(12))
        task.metadata["private_notes"] = "\n".join(f"Private note line {number}" for number in range(12))
        backend.update_task(task)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(96, 18)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            app.query_one("#work-item-tabs", TabbedContent).active = "work-item-info-tab"
            await pilot.pause()

            info = app.query_one("#work-item-info-read", VerticalScroll)
            await wait_for_no_horizontal_overflow(pilot, info)
            self.assertTrue(info.allow_vertical_scroll)
            self.assertGreater(info.max_scroll_y, 0)
            self.assertEqual(0, info.max_scroll_x)
            self.assertEqual(1, info.styles.scrollbar_size_vertical)

            info.focus()
            await pilot.press("pagedown")
            await pilot.pause()
            self.assertGreater(info.scroll_y, 0)

    async def test_short_read_only_details_scroll_without_horizontal_overflow(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(96, 18)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            details = app.query_one("#work-item-detail-scroll", VerticalScroll)
            await wait_for_no_horizontal_overflow(pilot, details)
            self.assertTrue(details.allow_vertical_scroll)
            self.assertGreater(details.max_scroll_y, 0)
            self.assertEqual(0, details.max_scroll_x)
            self.assertEqual(1, details.styles.scrollbar_size_vertical)

            details.focus()
            await pilot.press("pagedown")
            await pilot.pause()
            self.assertGreater(details.scroll_y, 0)

    async def test_short_inline_editor_auto_scrolls_to_fields_and_keeps_actions_reachable(self) -> None:
        backend = workflow_backend()

        with patch.object(backend, "supports_private_notes", return_value=True):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=(96, 18)) as pilot:
                await pilot.pause()
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.press("e")
                await pilot.pause()

                editor = app.query_one("#work-item-info-edit", VerticalScroll)
                private_notes = app.query_one("#work-item-edit-private-notes", TextArea)
                save = app.query_one("#work-item-edit-save", Button)
                cancel = app.query_one("#work-item-edit-cancel", Button)

                self.assertTrue(editor.allow_vertical_scroll)
                self.assertGreater(editor.max_scroll_y, 0)
                self.assertEqual(0, editor.max_scroll_x)
                self.assertEqual(1, editor.styles.scrollbar_size_vertical)

                private_notes.focus()
                await pilot.pause()
                self.assertGreater(editor.scroll_y, 0)
                self.assertTrue(private_notes.has_focus)
                self.assertLessEqual(save.region.bottom, app.screen.region.bottom)
                self.assertLessEqual(cancel.region.bottom, app.screen.region.bottom)
                self.assertGreater(save.region.width, 0)
                self.assertGreater(cancel.region.width, 0)

                # Scrolling and focusing must not disturb the recoverable draft.
                summary = app.query_one("#work-item-edit-summary", Input)
                summary.value = "keep this short-terminal draft"
                private_notes.focus()
                await pilot.pause()
                self.assertEqual("keep this short-terminal draft", summary.value)
                self.assertTrue(app.query_one(WorkItemsView).editing)

    async def test_short_detail_editor_scrolls_to_the_last_provider_dynamic_field(self) -> None:
        backend = workflow_backend()
        editable = backend.editable_task_fields() | {"components"}
        available = backend.available_task_fields() | {WorkItemColumn.COMPONENTS}

        with (
            patch.object(backend, "editable_task_fields", return_value=editable),
            patch.object(backend, "available_task_fields", return_value=available),
        ):
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=(96, 18)) as pilot:
                await pilot.pause()
                app.set_board_layout(BoardLayout.SPLIT)
                await pilot.press("e")
                await pilot.pause()
                app.query_one("#work-item-tabs", TabbedContent).active = "work-item-details-tab"
                await pilot.pause()

                details = app.query_one("#work-item-edit-scroll", VerticalScroll)
                status = app.query_one("#work-item-edit-status")
                components = app.query_one("#work-item-edit-components", Input)
                await wait_for_no_horizontal_overflow(pilot, details)
                self.assertGreater(
                    details.max_scroll_y,
                    0,
                    (details.virtual_size, details.size, app.query_one("#work-item-detail-edit").region),
                )
                self.assertEqual(0, details.max_scroll_x)
                self.assertEqual(1, details.styles.scrollbar_size_vertical)

                status.focus()
                await pilot.pause()
                components.focus()
                await wait_for_layout(
                    pilot,
                    lambda: components.region.bottom <= details.content_region.bottom,
                    message="components field never scrolled into view",
                )
                self.assertGreater(
                    details.scroll_y,
                    0,
                    (details.max_scroll_y, details.content_region, components.region),
                )
                self.assertTrue(components.has_focus)
                self.assertLessEqual(
                    components.region.bottom,
                    details.content_region.bottom,
                )

    async def test_narrow_terminal_clamps_the_divider_before_editor_controls_clip(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("e")
            await pilot.pause()
            view = app.query_one(WorkItemsView)

            for _ in range(20):
                view.action_grow_list()
            await pilot.pause()

            pane = app.query_one("#work-item-detail-pane")
            summary = app.query_one("#work-item-edit-summary", Input)
            save = app.query_one("#work-item-edit-save", Button)
            cancel = app.query_one("#work-item-edit-cancel", Button)

            # Keep the user's requested ratio for a later wider resize, while
            # applying a safe effective width to this narrow terminal.
            self.assertEqual(75, view.list_percent)
            self.assertGreaterEqual(pane.region.width, 39)
            self.assertGreaterEqual(summary.region.width, 30)
            self.assertLessEqual(save.region.right, pane.content_region.right)
            self.assertLessEqual(cancel.region.right, pane.content_region.right)
            self.assertEqual(1, app.query_one("#work-item-resizer").region.width)


if __name__ == "__main__":
    unittest.main()
