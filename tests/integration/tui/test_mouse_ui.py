"""Right-click a column, double-click a card.

These drive real mouse events through Textual's pilot rather than calling the
handlers, so the button and click-count routing is covered too.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from textual.geometry import Offset
from textual.pilot import Pilot
from textual.widgets import Input, OptionList

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.models import Task
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.menu import ContextMenuScreen, MenuItem, PromptScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui import provider_links
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets import card_fields
from pykantui.tui.widgets.card_fields import ROWS, Field
from pykantui.tui.widgets.column import BoardColumn
from pykantui.tui.widgets.work_item_fields import detail_field_visible

SIZE = (150, 40)


def every_field() -> list[Field]:
    return [field for row in ROWS for field in row]


def selector(key: str) -> str:
    return f"#detail-{key.replace('_', '-')}"


def enabled(app: KanbanApp) -> list[str]:
    """The fields the popup will currently let you type into."""
    return [
        field.key
        for field in every_field()
        if app.screen.query(selector(field.key)) and not app.screen.query_one(selector(field.key)).disabled
    ]


def config_of(*names: str) -> BoardConfig:
    return BoardConfig(
        columns=[ColumnConfig(column_id=index + 1, name=name, position=index) for index, name in enumerate(names)],
        reset_column=1,
        start_column=2,
        finish_column=len(names),
    )


def seeded() -> JsonBackend:
    backend = JsonBackend(config=config_of("To Do", "Doing", "Done"))
    backend.create_task(Task(task_id=1, title="first", column_id=1, description="the body"))
    backend.create_task(Task(task_id=2, title="second", column_id=1))
    backend.create_task(Task(task_id=3, title="third", column_id=2))
    return backend


def make_app(backend: JsonBackend | None = None) -> KanbanApp:
    return KanbanApp(backend=backend or seeded(), confirm_moves=False)


async def settle(pilot: Pilot[None]) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def choose(pilot: Pilot[None], label: str) -> None:
    """Pick a menu entry by its visible label."""
    options = pilot.app.screen.query_one(OptionList)
    index = next(
        position
        for position in range(options.option_count)
        if label in str(options.get_option_at_index(position).prompt)
    )
    options.highlighted = index
    await pilot.press("enter")


def labels(app: KanbanApp) -> list[str]:
    options = app.screen.query_one(OptionList)
    return [str(options.get_option_at_index(index).prompt) for index in range(options.option_count)]


class DoubleClickTests(unittest.IsolatedAsyncioTestCase):
    async def test_docker_arrow_click_copies_the_link_to_textual_clipboard(self) -> None:
        backend = seeded()
        task = backend.get_task_by_id(1)
        assert task is not None
        url = "https://example.test/issues/1"
        task.metadata["url"] = url
        backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            with patch.object(provider_links, "_running_in_container", return_value=True):
                await pilot.click("#card-1 .provider-issue-link")
                await pilot.pause()

            copied = app.clipboard

        self.assertEqual(url, copied)

    async def test_kanban_arrow_click_routes_through_host_aware_launcher(self) -> None:
        backend = seeded()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/1"
        backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            link = app.query_one("#card-1 .provider-issue-link")
            with (
                patch.object(provider_links, "launch_external_url", create=True, return_value=True) as launcher,
            ):
                await pilot.click(link)
                await pilot.pause()

        launcher.assert_called_once_with(app, "https://example.test/issues/1")

    async def test_kanban_card_provider_arrow_opens_cached_https_url(self) -> None:
        backend = seeded()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/1"
        backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            root_screen = app.screen
            root_depth = len(app.screen_stack)
            link = app.query_one("#card-1 .provider-issue-link")
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.click(link)
                await pilot.pause()
            screen_after = app.screen
            depth_after = len(app.screen_stack)

        self.assertEqual("↗", str(link.render()))
        launcher.assert_called_once_with(app, "https://example.test/issues/1")
        self.assertIs(root_screen, screen_after)
        self.assertEqual(root_depth, depth_after)

    async def test_kanban_provider_arrows_only_appear_for_the_active_card(self) -> None:
        backend = seeded()
        for task_id in (1, 2):
            task = backend.get_task_by_id(task_id)
            assert task is not None
            task.metadata["url"] = f"https://example.test/issues/{task_id}"
            backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            first = app.query_one("#card-1 .provider-issue-link")
            second = app.query_one("#card-2 .provider-issue-link")

            self.assertEqual(1.0, first.styles.opacity)
            self.assertEqual(0.0, second.styles.opacity)

            await pilot.hover("#card-2")
            await pilot.pause()
            self.assertEqual(0.0, second.styles.opacity)

            with patch.object(provider_links, "launch_external_url", return_value=True) as hidden_launcher:
                await pilot.click(second)
                await pilot.pause()
            hidden_launcher.assert_not_called()

            await pilot.click("#card-2")
            await settle(pilot)
            self.assertEqual(0.0, first.styles.opacity)
            self.assertEqual(1.0, second.styles.opacity)

            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.press("ctrl+o")
                await pilot.pause()

        launcher.assert_called_once_with(app, "https://example.test/issues/2")

    async def test_detail_keeps_the_selected_kanban_provider_arrow_visible(self) -> None:
        backend = seeded()
        task = backend.get_task_by_id(2)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/2"
        backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#card-2", times=2)
            await pilot.pause()
            card_link = app.query_one("#card-2 .provider-issue-link")

            self.assertIsInstance(app.screen, TaskDetailScreen)
            self.assertEqual(1.0, card_link.styles.opacity)

    async def test_detail_popup_provider_arrow_is_clickable_and_keyboard_accessible(self) -> None:
        backend = seeded()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/1"
        backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()
            link = app.screen.query_one("#detail-provider-link")
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                link.focus()
                await pilot.press("enter")
                await pilot.pause()
            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual("↗", str(link.render()))
        launcher.assert_called_once_with(app, "https://example.test/issues/1")

    async def test_detail_popup_ctrl_o_opens_the_provider_issue(self) -> None:
        backend = seeded()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/1"
        backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()
            with patch.object(provider_links, "launch_external_url", return_value=True) as launcher:
                await pilot.press("ctrl+o")
                await pilot.pause()

        launcher.assert_called_once_with(app, "https://example.test/issues/1")

    async def test_provider_arrow_stays_one_cell_across_every_theme(self) -> None:
        backend = seeded()
        task = backend.get_task_by_id(1)
        assert task is not None
        task.metadata["url"] = "https://example.test/issues/1"
        backend.update_task(task)
        app = make_app(backend)

        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot)
            widths: dict[str, int] = {}
            for theme in sorted(app.available_themes):
                app.theme = theme
                await pilot.pause()
                link = app.query_one("#card-1 .provider-issue-link")
                widths[theme] = link.region.width

        self.assertTrue(widths)
        self.assertEqual({1}, set(widths.values()), widths)

    async def test_double_clicking_a_card_opens_its_detail(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#card-1", times=2)
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)

            await pilot.press("escape")
            await settle(pilot)

    async def test_a_single_click_does_not_open_it(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#card-1")
            await settle(pilot)

            self.assertNotIsInstance(app.screen, TaskDetailScreen)

    async def test_v_opens_it_from_the_keyboard(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)

            await pilot.press("escape")
            await settle(pilot)

    async def test_the_detail_shows_the_column_and_the_description(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, TaskDetailScreen)
            self.assertEqual(screen.column_name, "To Do")
            self.assertEqual(screen.task_.description, "the body")

            await pilot.press("escape")
            await settle(pilot)

    async def test_dependencies_are_listed_both_ways(self) -> None:
        backend = JsonBackend(config=config_of("To Do", "Doing", "Done"))
        backend.create_task(Task(task_id=1, title="blocker", column_id=1))
        backend.create_task(Task(task_id=2, title="blocked", column_id=1, blocked_by=[1]))
        stored = backend.get_task_by_id(1)
        assert stored is not None
        stored.blocking = [2]
        backend.update_task(stored)
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j")
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, TaskDetailScreen)
            blockers = [task.task_id for task in screen.blockers]

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(blockers, [1])

    async def test_detail_uses_the_same_field_visibility_as_split(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, TaskDetailScreen)
            available = app.backend.available_task_fields()
            expected = {
                field.key
                for field in every_field()
                if detail_field_visible(
                    field,
                    value=card_fields.value_of(
                        field,
                        screen.task_,
                        app.column_choices(),
                        screen.blockers,
                    ),
                    available=available,
                )
            }
            actual = {
                field.key
                for field in every_field()
                if app.screen.query(selector(field.key))
            }

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(expected, actual)

    async def test_nothing_is_editable_until_you_ask(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()

            live = enabled(app)

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(live, [])

    async def test_e_turns_the_editable_fields_on_in_place(self) -> None:
        """The same popup, not a different screen — what you read is what you edit."""
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskDetailScreen)
            live = enabled(app)

            await pilot.press("escape")
            await settle(pilot)

        policy = app.editor_policy()
        self.assertEqual(
            live,
            [field.key for field in every_field() if field.editable and policy.allows_field(field)],
        )

    async def test_e_on_a_card_opens_the_popup_already_editing(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("e")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, TaskDetailScreen)
            editing = screen.editing

            await pilot.press("escape")
            await settle(pilot)

        self.assertTrue(editing)

    async def test_saving_writes_the_edited_fields_back(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            app.screen.query_one("#detail-summary", Input).value = "renamed"
            app.screen.query_one("#detail-labels", Input).value = "auth, api"
            await pilot.press("ctrl+s")
            await settle(pilot)

            self.assertNotIsInstance(app.screen, TaskDetailScreen)

        stored = app.backend.get_task_by_id(1)
        assert stored is not None
        self.assertEqual(stored.title, "renamed")
        self.assertEqual(stored.metadata["labels"], ["auth", "api"])

    async def test_a_read_only_backend_never_enables_a_field(self) -> None:
        backend = seeded()
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, TaskDetailScreen)
            screen.writable = False
            await pilot.press("e")
            await pilot.pause()

            live = enabled(app)
            editing = screen.editing

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(live, [])
        self.assertFalse(editing)


class ColumnMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_right_clicking_a_column_opens_the_menu(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-2", button=3)
            await pilot.pause()

            self.assertIsInstance(app.screen, ContextMenuScreen)
            screen = app.screen
            assert isinstance(screen, ContextMenuScreen)
            self.assertEqual(screen.menu_title, "Doing")

            await pilot.press("escape")
            await settle(pilot)

    async def test_a_left_click_does_not_open_it(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-2")
            await settle(pilot)

            self.assertNotIsInstance(app.screen, ContextMenuScreen)

    async def test_the_menu_offers_the_editing_actions(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-1", button=3)
            await pilot.pause()

            entries = labels(app)

            await pilot.press("escape")
            await settle(pilot)

        for expected in ("New card here", "Rename column", "Add column after", "Delete column"):
            self.assertTrue(any(expected in entry for entry in entries), f"missing {expected}: {entries}")

    async def test_escape_closes_without_doing_anything(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            before = [column.title for column in app.query(BoardColumn).results()]
            await pilot.click("#column-1", button=3)
            await pilot.pause()
            await pilot.press("escape")
            await settle(pilot)

            after = [column.title for column in app.query(BoardColumn).results()]

        self.assertEqual(before, after)

    async def test_new_card_here_opens_the_editor_on_that_column(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-2", button=3)
            await pilot.pause()
            await choose(pilot, "New card here")
            await settle(pilot)

            self.assertIsNotNone(app.screen.query("#edit-title"))

            await pilot.press("escape")
            await settle(pilot)

    async def test_collapse_from_the_menu(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-3", button=3)
            await pilot.pause()
            await choose(pilot, "Collapse")
            await settle(pilot)

            self.assertTrue(app.query_one("#column-3", BoardColumn).collapsed)

    async def test_a_collapsed_column_offers_expand_instead(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.board.set_collapsed(app.query_one("#column-3", BoardColumn), True)
            await settle(pilot)

            await pilot.click("#column-3", button=3)
            await pilot.pause()
            entries = labels(app)

            await pilot.press("escape")
            await settle(pilot)

        self.assertTrue(any("Expand" in entry for entry in entries))

    async def test_rename_changes_the_heading(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-2", button=3)
            await pilot.pause()
            await choose(pilot, "Rename column")
            await pilot.pause()

            self.assertIsInstance(app.screen, PromptScreen)
            await pilot.press(*"Building")
            await pilot.press("enter")
            await settle(pilot)

            titles = [column.title for column in app.query(BoardColumn).results()]

        self.assertIn("Building", titles)

    async def test_add_column_after_inserts_next_to_it(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-1", button=3)
            await pilot.pause()
            await choose(pilot, "Add column after")
            await pilot.pause()
            await pilot.press(*"Triage")
            await pilot.press("enter")
            await settle(pilot)

            titles = [column.title for column in app.query(BoardColumn).results()]

        self.assertEqual(titles, ["To Do", "Triage", "Doing", "Done"])

    async def test_hide_removes_it_from_the_board(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-3", button=3)
            await pilot.pause()
            await choose(pilot, "Hide column")
            await settle(pilot)

            titles = [column.title for column in app.query(BoardColumn).results()]

        self.assertEqual(titles, ["To Do", "Doing"])

    async def test_delete_column_asks_first_and_rehomes_the_cards(self) -> None:
        backend = seeded()
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-2", button=3)
            await pilot.pause()
            await choose(pilot, "Delete column")
            await pilot.pause()

            self.assertIsInstance(app.screen, ContextMenuScreen)
            await choose(pilot, "Delete column")
            await settle(pilot)

            titles = [column.title for column in app.query(BoardColumn).results()]

        self.assertEqual(titles, ["To Do", "Done"])
        stranded = backend.get_task_by_id(3)
        assert stranded is not None
        self.assertEqual(stranded.column_id, 1)

    async def test_cancelling_the_delete_keeps_the_column(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-2", button=3)
            await pilot.pause()
            await choose(pilot, "Delete column")
            await pilot.pause()
            await choose(pilot, "Cancel")
            await settle(pilot)

            titles = [column.title for column in app.query(BoardColumn).results()]

        self.assertEqual(titles, ["To Do", "Doing", "Done"])

    async def test_delete_all_cards_empties_the_column(self) -> None:
        backend = seeded()
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-1", button=3)
            await pilot.pause()
            await choose(pilot, "Delete all cards")
            await pilot.pause()
            await choose(pilot, "Delete cards")
            await settle(pilot)

            remaining = app.query_one("#column-1", BoardColumn).task_count

        self.assertEqual(remaining, 0)
        self.assertEqual(len(backend.get_tasks()), 1)

    async def test_a_right_click_does_not_pick_a_card_up(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#card-1", button=3)
            await pilot.pause()

            # The menu opened, and no drag is in progress underneath it.
            self.assertIsInstance(app.screen, ContextMenuScreen)
            self.assertFalse(app.board._dragging)

            await pilot.press("escape")
            await settle(pilot)


if __name__ == "__main__":
    unittest.main()


class KeyboardMenuTests(unittest.IsolatedAsyncioTestCase):
    """The same menu without a mouse — some terminals never send right-clicks."""

    async def test_comma_opens_the_menu_for_the_focused_column(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("l")  # focus a card in Doing
            await settle(pilot)
            await pilot.press("comma")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, ContextMenuScreen)
            title = screen.menu_title

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(title, "Doing")

    async def test_it_offers_the_same_actions_as_the_right_click(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("comma")
            await pilot.pause()
            by_key = labels(app)
            await pilot.press("escape")
            await settle(pilot)

            await pilot.click("#column-1", button=3)
            await pilot.pause()
            by_mouse = labels(app)
            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(by_key, by_mouse)

    async def test_it_works_on_an_empty_board(self) -> None:
        app = make_app(JsonBackend(config=config_of("To Do", "Doing")))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("comma")
            await pilot.pause()

            self.assertIsInstance(app.screen, ContextMenuScreen)

            await pilot.press("escape")
            await settle(pilot)


class DropdownTests(unittest.IsolatedAsyncioTestCase):
    """The menu drops from the column it belongs to, not from the middle."""

    async def test_context_menu_fits_a_terminal_narrower_than_its_default_width(self) -> None:
        app = make_app()

        async with app.run_test(size=(30, 12)) as pilot:
            await settle(pilot)
            await app.push_screen(
                ContextMenuScreen(
                    "Actions",
                    [MenuItem("view", "View"), MenuItem("edit", "Edit")],
                    anchor_at=Offset(28, 10),
                )
            )
            await pilot.pause()

            dialog = app.screen.query_one("#menu-dialog")
            contained = app.screen.region.contains_region(dialog.region)
            region = dialog.region
            screen_region = app.screen.region

            await pilot.press("escape")
            await settle(pilot)

        self.assertTrue(contained, f"menu {region!r} escaped narrow screen {screen_region!r}")

    async def test_context_menu_reflows_when_terminal_resizes_while_open(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await app.push_screen(
                ContextMenuScreen(
                    "Actions",
                    [MenuItem("view", "View"), MenuItem("edit", "Edit")],
                    anchor_at=Offset(SIZE[0] - 2, SIZE[1] - 2),
                )
            )
            await pilot.pause()

            await pilot.resize_terminal(30, 12)
            await pilot.pause()
            dialog = app.screen.query_one("#menu-dialog")
            contained = app.screen.region.contains_region(dialog.region)
            region = dialog.region
            screen_region = app.screen.region

            await pilot.press("escape")
            await settle(pilot)

        self.assertTrue(contained, f"menu {region!r} escaped resized screen {screen_region!r}")

    async def test_rapid_right_clicks_open_only_one_context_menu(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            root_depth = len(app.screen_stack)

            await pilot.click("#column-1", button=3, times=3)
            await pilot.pause()
            stacked_depth = len(app.screen_stack)

            await pilot.press("escape")
            await pilot.pause()
            depth_after_one_escape = len(app.screen_stack)

            while len(app.screen_stack) > root_depth:
                await pilot.press("escape")
                await pilot.pause()

        self.assertEqual(root_depth + 1, stacked_depth)
        self.assertEqual(root_depth, depth_after_one_escape)

    async def test_the_menu_opens_under_its_own_column(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            column = app.query_one("#column-2", BoardColumn)
            left, top = column.region.x, column.region.y

            await pilot.click("#column-2", button=3)
            await pilot.pause()
            dialog = app.screen.query_one("#menu-dialog")
            region = dialog.region

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(region.x, left)
        self.assertGreater(region.y, top)

    async def test_a_menu_on_the_last_column_stays_on_screen(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-3", button=3)
            await pilot.pause()
            dialog = app.screen.query_one("#menu-dialog")
            region = dialog.region
            width = app.screen.size.width

            await pilot.press("escape")
            await settle(pilot)

        self.assertLessEqual(region.right, width)

    async def test_a_menu_with_no_anchor_is_centred(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            # The delete confirmation has no anchor: it is a dialog, not a menu.
            await pilot.click("#column-2", button=3)
            await pilot.pause()
            await choose(pilot, "Delete column")
            await pilot.pause()

            dialog = app.screen.query_one("#menu-dialog")
            off_centre = abs(dialog.region.x + dialog.region.width // 2 - app.screen.size.width // 2)

            await choose(pilot, "Cancel")
            await settle(pilot)

        self.assertLessEqual(off_centre, 1)

    async def test_clicking_a_collapsed_strip_expands_without_a_menu(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.board.set_collapsed(app.query_one("#column-3", BoardColumn), True)
            await settle(pilot)

            await pilot.click("#column-3 .column-strip")
            await settle(pilot)

            self.assertNotIsInstance(app.screen, ContextMenuScreen)
            self.assertFalse(app.query_one("#column-3", BoardColumn).collapsed)
