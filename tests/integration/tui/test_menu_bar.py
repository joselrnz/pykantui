"""The top bar, and filtering through it."""

from __future__ import annotations

import contextlib
import inspect
import os
import tempfile
import unittest
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from textual.binding import Binding
from textual.css.stylesheet import Stylesheet
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Input, Label, Select

from pykantui.config import DEFAULT_THEME, BoardConfig, ColumnConfig
from pykantui.core.filters import CardFilter, FilterState, SortKey
from pykantui.models import Edges, MenuLevel, Task
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.grouped_palette import GroupedCommandPalette
from pykantui.pages.menu import ContextMenuScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker import get
from pykantui.tracker.filter_fields import FilterFieldSpec
from pykantui.tui import themes
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.app_header import AppHeader
from pykantui.tui.widgets.column import BoardColumn
from pykantui.tui.widgets.dropdowns import LabelledInput
from pykantui.tui.widgets.work_items import WorkItemsView

SIZE = (150, 40)


@contextlib.contextmanager
def sandbox() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        previous = os.environ.get("PYKANTUI_HOME")
        os.environ["PYKANTUI_HOME"] = directory
        try:
            yield Path(directory)
        finally:
            if previous is None:
                del os.environ["PYKANTUI_HOME"]
            else:
                os.environ["PYKANTUI_HOME"] = previous


def config_of(*names: str) -> BoardConfig:
    return BoardConfig(
        columns=[ColumnConfig(column_id=index + 1, name=name, position=index) for index, name in enumerate(names)],
        reset_column=1,
        start_column=2,
        finish_column=len(names),
    )


def seeded() -> JsonBackend:
    backend = JsonBackend(config=config_of("To Do", "Doing", "Done"))
    backend.create_task(Task(task_id=1, title="Upgrade Postgres", column_id=1, description="the runbook"))
    backend.create_task(
        Task(task_id=2, title="Fix timezone drift", column_id=1, due_date=date.today() - timedelta(days=2))
    )
    backend.create_task(Task(task_id=3, title="Rate-limit the API", column_id=1, blocked_by=[1]))
    backend.create_task(Task(task_id=4, title="Read the clone", column_id=2))
    return backend


def make_app(backend: JsonBackend | None = None) -> KanbanApp:
    return KanbanApp(backend=backend or seeded(), confirm_moves=False)


class ProviderMenuBackend(JsonBackend):
    """Local data with a real provider's filter contract."""

    def __init__(self, provider: str, provider_config: dict[str, object] | None = None) -> None:
        super().__init__(config=config_of("To Do", "Doing", "Done"))
        self.spec = get(provider).spec
        self.provider_config = provider_config or {}

    def provider_filter_fields(self) -> tuple[FilterFieldSpec, ...]:
        return self.spec.filter_fields(self.provider_config)


class QueryProviderMenuBackend(ProviderMenuBackend):
    """Provider-shaped local data with Jira query controls enabled."""

    supports_query = True


class SyncProviderMenuBackend(ProviderMenuBackend):
    """Provider-shaped local data whose header and palette expose Sync."""

    supports_sync = True

    def display_kind(self) -> str:
        return "Jira"


async def settle(pilot: Pilot[None]) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def shown(app: KanbanApp) -> list[int]:
    return [card.task_.task_id for column in app.query(BoardColumn).results() for card in column.cards()]


def status(app: KanbanApp) -> str:
    return str(app.menu_bar.query_one("#bar-status", Label).content)


class LevelTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_starts_collapsed(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            bar = app.menu_bar

            self.assertEqual(bar.level, MenuLevel.COLLAPSED)
            self.assertFalse(bar.query_one("#bar-search", Input).display)

    async def test_f2_cycles_through_the_three_levels(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            levels = [app.menu_bar.level]
            for _ in range(3):
                await pilot.press("f2")
                await settle(pilot)
                levels.append(app.menu_bar.level)

        self.assertEqual(levels, [MenuLevel.COLLAPSED, MenuLevel.TOOLBAR, MenuLevel.EXPANDED, MenuLevel.COLLAPSED])

    async def test_the_toolbar_shows_search_and_the_menus(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            bar = app.menu_bar

            self.assertTrue(bar.query_one("#bar-search", Input).display)
            self.assertTrue(bar.query_one("#bar-menu-filter", Label).display)
            self.assertFalse(bar.query_one("#bar-panel").display)

    async def test_expanded_shows_the_chip_panel(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await pilot.press("f2")
            await settle(pilot)

            self.assertTrue(app.menu_bar.query_one("#bar-panel").display)
            # Local boards have status plus the shared sort/state/saved boxes.
            self.assertEqual(len(app.menu_bar.query(Select)), 4)
            self.assertEqual(len(app.menu_bar.query(".chip")), 3)

    async def test_the_count_is_shown_at_every_level(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            seen = [status(app)]
            for _ in range(2):
                await pilot.press("f2")
                await settle(pilot)
                seen.append(status(app))

        for text in seen:
            self.assertIn("4 cards", text)

    async def test_the_level_is_remembered_in_the_config(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            app = make_app(JsonBackend(config=config))

            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                await pilot.press("f2")
                await settle(pilot)

            saved = BoardConfig.load().menu_level

        self.assertEqual(saved, MenuLevel.TOOLBAR)

    async def test_a_board_opens_at_the_remembered_level(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.menu_level = MenuLevel.EXPANDED
            config.save()

            app = make_app(JsonBackend(config=BoardConfig.load()))
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                level = app.menu_bar.level

        self.assertEqual(level, MenuLevel.EXPANDED)


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_typing_filters_the_board(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)

            app.menu_bar.query_one("#bar-search", Input).value = "postgres"
            await settle(pilot)

            self.assertEqual(shown(app), [1])

    async def test_the_count_says_how_many_are_hidden(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            app.menu_bar.query_one("#bar-search", Input).value = "postgres"
            await settle(pilot)

            text = status(app)

        self.assertIn("1 of 4", text)
        self.assertIn("postgres", text)

    async def test_it_searches_descriptions_too(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            app.menu_bar.query_one("#bar-search", Input).value = "runbook"
            await settle(pilot)

            self.assertEqual(shown(app), [1])

    async def test_clearing_the_box_restores_every_card(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            field = app.menu_bar.query_one("#bar-search", Input)
            field.value = "postgres"
            await settle(pilot)
            field.value = ""
            await settle(pilot)

            self.assertEqual(sorted(shown(app)), [1, 2, 3, 4])

    async def test_slash_focuses_the_box_and_opens_the_bar(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("slash")
            await settle(pilot)

            self.assertNotEqual(app.menu_bar.level, MenuLevel.COLLAPSED)
            focused = app.focused
            self.assertEqual(focused.id if focused else None, "bar-search")


class DropdownTests(unittest.IsolatedAsyncioTestCase):
    """The filter panel: labelled dropdowns plus a row of toggles."""

    @staticmethod
    async def expand(pilot: Pilot[None]) -> None:
        await pilot.press("f2")
        await pilot.press("f2")
        await settle(pilot)

    @staticmethod
    def select(app: KanbanApp, widget_id: str) -> Select[Any]:
        return app.menu_bar.query_one(f"#{widget_id}", Select)

    async def test_picking_a_state_filters_the_board(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)

            self.select(app, "filter-state").value = FilterState.OVERDUE.value
            await settle(pilot)

            self.assertEqual(shown(app), [2])
            self.assertEqual(app.view.card_filter.states, [FilterState.OVERDUE])

    async def test_the_dropdown_reflects_the_active_filter(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            self.select(app, "filter-state").value = FilterState.OVERDUE.value
            await settle(pilot)

            self.assertEqual(self.select(app, "filter-state").value, FilterState.OVERDUE.value)

    async def test_clearing_the_dropdown_restores_every_card(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            state = self.select(app, "filter-state")
            state.value = FilterState.OVERDUE.value
            await settle(pilot)

            state.value = Select.NULL
            await settle(pilot)

            self.assertEqual(sorted(shown(app)), [1, 2, 3, 4])
            self.assertFalse(app.view.card_filter.states)

    async def test_a_dropdown_replaces_rather_than_accumulates(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            state = self.select(app, "filter-state")
            state.value = FilterState.OVERDUE.value
            await settle(pilot)
            state.value = FilterState.BLOCKED.value
            await settle(pilot)

            self.assertEqual(app.view.card_filter.states, [FilterState.BLOCKED])
            self.assertEqual(shown(app), [3])

    async def test_blocked_uses_dependency_state(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            self.select(app, "filter-state").value = FilterState.BLOCKED.value
            await settle(pilot)

            self.assertEqual(shown(app), [3])

    async def test_the_sort_dropdown_reorders_within_the_column(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            self.select(app, "filter-sort").value = SortKey.TITLE.value
            await settle(pilot)

            self.assertEqual(app.view.sort, SortKey.TITLE)
            todo = [card.task_.title for card in app.query_one("#column-1", BoardColumn).cards()]

        self.assertEqual(todo, sorted(todo, key=str.casefold))

    async def test_clear_resets_filter_and_sort(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            self.select(app, "filter-state").value = FilterState.OVERDUE.value
            self.select(app, "filter-sort").value = SortKey.TITLE.value
            await settle(pilot)

            await pilot.click("#chip-act-clear")
            await settle(pilot)

            self.assertFalse(app.view.active)
            self.assertEqual(sorted(shown(app)), [1, 2, 3, 4])

    async def test_clear_resets_the_dropdowns_too(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            self.select(app, "filter-state").value = FilterState.OVERDUE.value
            await settle(pilot)

            await pilot.click("#chip-act-clear")
            await settle(pilot)

            self.assertIs(self.select(app, "filter-state").value, Select.NULL)

    async def test_reverse_flips_the_order(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            before = [card.task_.task_id for card in app.query_one("#column-1", BoardColumn).cards()]

            await pilot.click("#chip-act-reverse")
            await settle(pilot)
            after = [card.task_.task_id for card in app.query_one("#column-1", BoardColumn).cards()]

        self.assertEqual(after, list(reversed(before)))

    async def test_the_dropdowns_carry_a_label_and_a_key_hint(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            state = self.select(app, "filter-state")
            titles = (state.border_title, state.border_subtitle)

        self.assertEqual(titles, ("State", "(y)"))

    async def test_provider_dropdowns_are_absent_on_a_local_board(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            provider_dropdowns = app.menu_bar.query(".provider-filter")

            self.assertEqual(0, len(provider_dropdowns))

    async def test_y_focuses_the_state_dropdown_and_opens_the_panel(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("y")
            await settle(pilot)

            self.assertEqual(app.menu_bar.level, MenuLevel.EXPANDED)
            focused = app.focused
            self.assertEqual(focused.id if focused else None, "filter-state")

    async def test_s_focuses_status_the_way_jiratui_does(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("s")
            await settle(pilot)

            focused = app.focused
            self.assertEqual(focused.id if focused else None, "filter-status")

    async def test_every_shortcut_is_unique(self) -> None:
        """Two fields claiming one key means one of them is unreachable."""
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            # Every labelled control, not just the dropdowns: the Active
            # Sprint checkbox carries a hint too, and collided with Saved
            # precisely because it was not a .dropdown.
            hints = [
                str(widget.border_subtitle)
                for widget in app.menu_bar.query(".dropdown, .input-checkbox").results()
                if widget.border_subtitle
            ]

        self.assertEqual(len(hints), len(set(hints)), f"duplicate shortcut in {hints}")

    async def test_the_status_dropdown_filters_to_one_column(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            self.select(app, "filter-status").value = "2"
            await settle(pilot)

            self.assertEqual(shown(app), [4])

    async def test_the_key_field_matches_a_card_id(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            app.menu_bar.query_one("#filter-key", Input).value = "3"
            await settle(pilot)

            self.assertEqual(shown(app), [3])

    async def test_a_created_range_filters_by_date(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            app.menu_bar.query_one("#filter-created-from", Input).value = tomorrow
            await settle(pilot)

            self.assertEqual(shown(app), [])

    async def test_an_unparseable_date_is_simply_not_a_filter(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            app.menu_bar.query_one("#filter-created-from", Input).value = "not-a-date"
            await settle(pilot)

            self.assertEqual(sorted(shown(app)), [1, 2, 3, 4])

    async def test_local_board_hides_provider_query_fields(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            self.assertFalse(app.menu_bar.query("#filter-query"))
            self.assertFalse(app.menu_bar.query("#filter-sprint"))

    async def test_shared_header_fields_remain_on_a_local_board(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            bar = app.menu_bar
            wanted = [
                "filter-status",
                "filter-key",
                "filter-created-from",
                "filter-created-until",
                "filter-sort",
            ]
            missing = [name for name in wanted if not bar.query(f"#{name}")]

        self.assertEqual(missing, [])


class ProviderExpandedMenuTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def expand(pilot: Pilot[None]) -> None:
        await pilot.press("f2", "f2")
        await settle(pilot)

    async def test_jira_has_all_jira_boxes(self) -> None:
        app = make_app(ProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            bar = app.menu_bar
            titles = {widget.id: str(widget.border_title or "") for widget in bar.query(".provider-filter").results()}
            secondary_hints = {
                widget.id: str(widget.border_subtitle or "")
                for widget in bar.query("#filter-provider-priority, #filter-provider-labels").results()
            }
            query_disabled = bar.query_one("#filter-query").disabled
            sprint_disabled = bar.query_one("#filter-sprint").disabled
            search_disabled = bar.query_one("#filter-search").disabled

        self.assertEqual(
            {
                "filter-project": "Project",
                "filter-status": "Status",
                "filter-provider-assignee": "Assignee",
                "filter-provider-issue_type": "Type",
                "filter-provider-priority": "Priority",
                "filter-provider-labels": "Labels",
                "filter-key": "Issue Key",
                "filter-sprint": "",
                "filter-query": "JQL Query",
            },
            titles,
        )
        self.assertEqual(
            {"filter-provider-priority": "", "filter-provider-labels": ""},
            secondary_hints,
        )
        self.assertEqual((True, True, True), (query_disabled, sprint_disabled, search_disabled))

    async def test_trello_uses_board_list_member_and_labels(self) -> None:
        app = make_app(ProviderMenuBackend("trello"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            titles = {
                widget.id: str(widget.border_title or "") for widget in app.menu_bar.query(".provider-filter").results()
            }

        self.assertEqual(
            {
                "filter-project": "Board",
                "filter-status": "List",
                "filter-provider-assignee": "Member",
                "filter-provider-labels": "Labels",
                "filter-key": "Card ID",
            },
            titles,
        )

    async def test_monday_hides_unmapped_column_boxes(self) -> None:
        app = make_app(ProviderMenuBackend("monday"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            ids = {widget.id for widget in app.menu_bar.query(".provider-filter").results()}

        self.assertEqual({"filter-project", "filter-status", "filter-key"}, ids)

    async def test_monday_shows_configured_column_boxes_with_native_names(self) -> None:
        app = make_app(
            ProviderMenuBackend(
                "monday",
                {
                    "assignee_column": "people",
                    "type_column": "type",
                    "priority_column": "priority",
                    "labels_column": "labels",
                },
            )
        )

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await self.expand(pilot)
            titles = {
                widget.id: str(widget.border_title or "") for widget in app.menu_bar.query(".provider-filter").results()
            }

        self.assertEqual("People", titles["filter-provider-assignee"])
        self.assertEqual("Type", titles["filter-provider-issue_type"])
        self.assertEqual("Priority", titles["filter-provider-priority"])
        self.assertEqual("Labels", titles["filter-provider-labels"])


class SortedReorderTests(unittest.IsolatedAsyncioTestCase):
    """Sorting is a view, so J/K has nowhere to write while one is on."""

    async def test_reorder_is_disabled_while_sorted(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            card = app.board.card_by_id(1)
            assert card is not None
            self.assertTrue(card.check_action("reorder", ("down",)))

            app.view.sort = SortKey.TITLE
            await app.apply_view()
            await settle(pilot)

            card = app.board.card_by_id(1)
            assert card is not None
            self.assertFalse(card.check_action("reorder", ("down",)))

    async def test_the_manual_order_survives_a_sort(self) -> None:
        backend = seeded()
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            before = [task.task_id for task in backend.tasks_in_column(1)]

            app.view.sort = SortKey.TITLE
            await app.apply_view()
            await settle(pilot)

            app.view.sort = SortKey.MANUAL
            await app.apply_view()
            await settle(pilot)

            after = [task.task_id for task in backend.tasks_in_column(1)]
            on_screen = [card.task_.task_id for card in app.query_one("#column-1", BoardColumn).cards()]

        self.assertEqual(before, after)
        self.assertEqual(on_screen, before)


class MenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_provider_has_one_always_visible_header_action(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            header = app.query_one(AppHeader)
            sync = str(header.query_one("#app-header-sync", Label).content)
            duplicate_sync = bool(app.menu_bar.query("#bar-sync, #chip-act-sync"))

        self.assertEqual("⎇ Sync", sync)
        self.assertFalse(duplicate_sync)

    async def test_command_palette_contains_pykantui_and_textual_commands(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            titles = {command.title for command in app.get_system_commands(app.screen)}

        self.assertTrue(
            {
                "Theme",
                "Keys",
                "Home · ▥ Kanban",
                "Filter · Assignee…",
                "Sort · Due",
                "Columns · Add column…",
                "View · ▦ Split",
                "New card",
                "Reload files",
                "Sync with Jira…",
                "Help · Where things are stored",
            }.issubset(titles)
        )

    async def test_local_palette_does_not_offer_provider_sync(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            titles = {command.title for command in app.get_system_commands(app.screen)}

        self.assertFalse(any(title.startswith("Sync with ") for title in titles))

    async def test_the_header_menu_replaces_the_duplicate_bar_menu(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            duplicate = bool(app.menu_bar.query("#bar-menu"))
            await pilot.click("#app-header-menu")
            await pilot.pause()

            palette = app.screen

        self.assertFalse(duplicate)
        self.assertIsInstance(palette, GroupedCommandPalette)

    async def test_the_filter_label_opens_the_filter_menu(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            await pilot.click("#bar-menu-filter")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, ContextMenuScreen)
            labels = [item.label for item in screen.items]

            await pilot.press("escape")
            await settle(pilot)

        self.assertTrue(any("Overdue" in label for label in labels))
        self.assertTrue(any("Clear filters" in label for label in labels))

    async def test_jira_entries_are_hidden_on_a_local_board(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            await pilot.click("#bar-menu-filter")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, ContextMenuScreen)
            labels = [item.label for item in screen.items]

            await pilot.press("escape")
            await settle(pilot)

        self.assertFalse(any("Assignee" in label for label in labels))

    async def test_the_sort_menu_ticks_the_active_key(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.view.sort = SortKey.DUE
            await pilot.press("f2")
            await settle(pilot)
            await pilot.click("#bar-menu-sort")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, ContextMenuScreen)
            ticked = [item.label for item in screen.items if item.label.startswith("✓")]

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(len(ticked), 1)
        self.assertIn("Due", ticked[0])


class SavedFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_saved_filter_appears_in_the_menu_and_applies(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.saved_filters["My overdue"] = CardFilter(states=[FilterState.OVERDUE])
            config.save()

            app = make_app(JsonBackend(config=BoardConfig.load()))
            backend = app.backend
            assert isinstance(backend, JsonBackend)
            backend.create_task(Task(task_id=1, title="late", column_id=1, due_date=date.today() - timedelta(days=1)))
            backend.create_task(Task(task_id=2, title="fine", column_id=1))

            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                app._run_view_action("saved:My overdue")
                await settle(pilot)

                visible = shown(app)

        self.assertEqual(visible, [1])


if __name__ == "__main__":
    unittest.main()


class ThemeTests(unittest.IsolatedAsyncioTestCase):
    """The board is dark by default, and the theme is a saved preference."""

    async def test_the_default_theme_is_dark(self) -> None:
        self.assertEqual("cyberpunk", DEFAULT_THEME)
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)

            self.assertEqual(app.theme, DEFAULT_THEME)
            self.assertTrue(app.current_theme.dark)

    async def test_the_screen_uses_the_theme_background_not_the_surface(self) -> None:
        """The board must be the darkest thing; panels sit above it.

        Read from the generated colour system, not the Theme attributes: a
        built-in like textual-dark leaves background and surface unset and
        lets Textual derive them.
        """
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            screen_bg = app.screen.styles.background
            palette = app.current_theme.to_color_system().generate()

        self.assertEqual(screen_bg.hex.lower(), palette["background"].lower())
        self.assertNotEqual(palette["background"].lower(), palette["surface"].lower())

    async def test_a_configured_theme_is_used(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.theme = "gruvbox"
            config.save()

            app = make_app(JsonBackend(config=BoardConfig.load()))
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                theme = app.theme

        self.assertEqual(theme, "gruvbox")

    async def test_the_cyberpunk_theme_can_be_saved_and_used(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.theme = "cyberpunk"
            config.save()

            app = make_app(JsonBackend(config=BoardConfig.load()))
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                theme = app.theme
                palette = app.current_theme.to_color_system().generate()

        self.assertEqual(theme, "cyberpunk")
        self.assertEqual(palette["background"].lower(), "#0a0e14")
        self.assertEqual(palette["accent"].lower(), "#00c8ff")

    async def test_selecting_cyberpunk_from_the_palette_is_remembered(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            app = make_app(JsonBackend(config=config))
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                app.theme = "cyberpunk"
                await settle(pilot)

            saved = BoardConfig.load().theme

        self.assertEqual(saved, "cyberpunk")

    def test_cyberpunk_keeps_the_reference_component_tokens(self) -> None:
        theme = getattr(themes, "CYBERPUNK", None)

        self.assertIsNotNone(theme)
        assert theme is not None
        self.assertEqual(theme.variables["border"], "#6FB2FF")
        self.assertEqual(theme.variables["footer-key-foreground"], "#00C8FF")
        self.assertEqual(theme.variables["scrollbar-active"], "#0078DC")

    async def test_cyberpunk_dropdowns_match_the_reference_states(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.theme = "cyberpunk"
            config.save()
            app = make_app(JsonBackend(config=BoardConfig.load()))

            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                await pilot.press("f2", "f2")
                await settle(pilot)
                field = app.menu_bar.query_one("#filter-state", Select)
                default_border = field.styles.border.top
                background = field.styles.background
                padding = field.styles.padding

                field.focus()
                await pilot.pause()
                focused_border = field.styles.border.top

        self.assertEqual(default_border[0], "round")
        self.assertEqual(default_border[1].hex.lower(), "#6fb2ff")
        self.assertEqual(background.hex.lower(), "#0d1117")
        self.assertEqual((padding.top, padding.right, padding.bottom, padding.left), (0, 1, 0, 1))
        self.assertEqual(focused_border[0], "round")
        self.assertEqual(focused_border[1].hex.lower(), "#7bffff")

    async def test_cyberpunk_provider_fields_match_the_reference_colors(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.theme = "cyberpunk"
            config.save()
            app = make_app(QueryProviderMenuBackend("jira"))

            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                await pilot.press("f2", "f2")
                await settle(pilot)

                assignee = app.menu_bar.query_one("#filter-provider-assignee", Select)
                created_from = app.menu_bar.query_one("#filter-created-from", Input)
                sprint = app.menu_bar.query_one("#filter-sprint", Checkbox)
                search = app.menu_bar.query_one("#filter-search", Button)
                required = LabelledInput(
                    placeholder="Required",
                    title="Summary",
                    key="*",
                    widget_id="required-color-test",
                )
                await app.screen.mount(required)
                await pilot.pause()

                default_colors = (
                    assignee.styles.border.top[1].hex.lower(),
                    created_from.styles.border.top[1].hex.lower(),
                )
                backgrounds = (
                    assignee.styles.background.hex.lower(),
                    created_from.styles.background.hex.lower(),
                    search.styles.background.hex.lower(),
                )
                required_color = required.styles.border.top[1].hex.lower()

                assignee.focus()
                await pilot.pause()
                assignee_focus = assignee.styles.border.top[1].hex.lower()
                created_from.focus()
                await pilot.pause()
                date_focus = created_from.styles.border.top[1].hex.lower()
                sprint.focus()
                await pilot.pause()
                checkbox_focus = sprint.styles.border.top[1].hex.lower()

                sprint.value = True
                await pilot.pause()
                checked_style = sprint.get_component_rich_style("toggle--button")
                assert checked_style.bgcolor is not None
                assert checked_style.color is not None
                checked_background = checked_style.bgcolor.get_truecolor().hex.lower()
                checked_foreground = checked_style.color.get_truecolor().hex.lower()

        self.assertEqual(default_colors, ("#6fb2ff", "#6fb2ff"))
        self.assertEqual(backgrounds, ("#0d1117", "#0a0e14", "#00000000"))
        self.assertEqual(required_color, "#00c8ff")
        self.assertEqual((assignee_focus, date_focus, checkbox_focus), ("#7bffff",) * 3)
        self.assertEqual(checked_background, "#98c379")
        self.assertEqual(checked_foreground, "#00c8ff")

    async def test_cyberpunk_columns_use_the_raised_panel_colour(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.theme = "cyberpunk"
            config.save()
            app = make_app(JsonBackend(config=BoardConfig.load()))

            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                column_background = app.query(BoardColumn).first().styles.background
                palette = app.current_theme.to_color_system().generate()

        self.assertEqual(column_background.hex.lower(), palette["panel"].lower())

    async def test_cyberpunk_collapsed_columns_have_a_rounded_depth_outline(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.theme = "cyberpunk"
            config.save()
            backend = JsonBackend(config=BoardConfig.load())
            backend.set_column_collapsed(1, True)
            app = make_app(backend)

            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                column = app.query_one("#column-1", BoardColumn)
                outline = column.styles.outline
                strip_border = column.query_one(".column-strip").styles.border
                palette = app.current_theme.to_color_system().generate()

        self.assertEqual(outline.top[0], "round")
        self.assertEqual(outline.top[1].hex.lower(), palette["background"].lower())
        self.assertEqual(strip_border.top[0], "round")
        self.assertEqual(strip_border.top[1].hex.lower(), palette["primary"].lower())

    async def test_an_unknown_theme_falls_back_rather_than_crashing(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            config.theme = "not-a-theme"
            config.save()

            app = make_app(JsonBackend(config=BoardConfig.load()))
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                theme = app.theme

        self.assertEqual(theme, DEFAULT_THEME)


class ShortcutSafetyTests(unittest.IsolatedAsyncioTestCase):
    """Filter shortcuts must not take keys the board and cards already use.

    They did: (k) and (j) shadowed move-focus-up/down, (e) shadowed edit and
    (v) shadowed the card detail. Nothing failed loudly — the board simply
    stopped navigating.
    """

    @staticmethod
    def _keys(bindings: Sequence[object]) -> set[str]:
        """Keys declared by a widget's BINDINGS, which may be tuples or Bindings."""
        keys: set[str] = set()
        for binding in bindings:
            key = binding.key if isinstance(binding, Binding) else str(binding[0])  # type: ignore[index]
            keys.update(part.strip() for part in key.split(","))
        return keys

    @staticmethod
    def _filter_bindings() -> list[Binding]:
        return [
            binding
            for binding in KanbanApp.BINDINGS
            if isinstance(binding, Binding) and "focus_filter" in str(binding.action)
        ]

    async def test_filter_shortcuts_do_not_shadow_board_or_card_keys(self) -> None:
        from pykantui.tui.widgets.board import KanbanBoard
        from pykantui.tui.widgets.card import TaskCard

        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)

        filter_keys = {part.strip() for binding in self._filter_bindings() for part in binding.key.split(",")}
        taken = self._keys(KanbanBoard.BINDINGS) | self._keys(TaskCard.BINDINGS)

        self.assertEqual(filter_keys & taken, set(), "filter shortcut shadows a board key")

    async def test_provider_open_uses_ctrl_o_in_every_item_view(self) -> None:
        """Plain ``o`` belongs only to Sort; provider links use one chord."""
        from pykantui.tui.widgets.card import TaskCard

        for surface in (TaskCard, WorkItemsView, TaskDetailScreen):
            with self.subTest(surface=surface.__name__):
                bindings = [
                    binding
                    for binding in surface.BINDINGS
                    if isinstance(binding, Binding) and binding.action == "open_provider"
                ]
                self.assertEqual(1, len(bindings))
                self.assertEqual("ctrl+o", bindings[0].key)

    async def test_no_filter_shortcut_is_a_priority_binding(self) -> None:
        """A priority binding fires even while an Input has focus.

        With one, typing "p" into the search box jumps focus instead of
        entering a letter.
        """
        offenders = [binding.key for binding in self._filter_bindings() if binding.priority]

        self.assertEqual(offenders, [])

    async def test_j_and_k_still_move_the_focus(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            first = app.focused
            await pilot.press("j")
            await settle(pilot)
            second = app.focused

        self.assertIsNotNone(first)
        self.assertIsNot(first, second)

    async def test_typing_a_shortcut_letter_into_search_types_it(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("slash")
            await settle(pilot)
            await pilot.press("p", "o", "s")
            await settle(pilot)

            typed = app.menu_bar.query_one("#bar-search", Input).value

        self.assertEqual(typed, "pos")


class StylesheetTests(unittest.IsolatedAsyncioTestCase):
    """The stylesheet must parse.

    A single bad declaration fails the *whole* sheet, and Textual then falls
    back to its defaults: no theme, no layout, square buttons. The error goes
    to stderr, which a TUI paints over immediately — so nothing looks broken
    except everything. This has happened twice: a truncated file, and
    `border: round $text-muted`, which is an auto-contrast value that is legal
    for text but not for a border.
    """

    async def test_the_stylesheet_parses_against_the_real_theme(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            variables = app.get_css_variables()

        sheet = Stylesheet(variables=variables)
        source = Path(KanbanApp.CSS_PATH)
        if not source.is_absolute():
            source = Path(inspect.getfile(KanbanApp)).parent / source
        sheet.add_source(source.read_text(encoding="utf-8"), read_from=(str(source), ""))

        sheet.parse()  # raises StylesheetParseError if any declaration is bad

    async def test_the_theme_actually_applied(self) -> None:
        """If the sheet had failed, the screen would sit on $surface instead."""
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            background = app.screen.styles.background
            palette = app.current_theme.to_color_system().generate()

        self.assertEqual(background.hex.lower(), palette["background"].lower())
        self.assertNotEqual(background.hex.lower(), palette["surface"].lower())

    async def test_the_dialog_rules_are_present(self) -> None:
        """Nine detail rules were once deleted by an edit and nothing noticed."""
        source = Path(inspect.getfile(KanbanApp)).parent / KanbanApp.CSS_PATH
        css = source.read_text(encoding="utf-8")

        missing = [
            selector
            for selector in (
                "#detail-dialog",
                "#detail-body",
                "#detail-headline",
                "#detail-notes",
                ".field-row",
                "#detail-buttons",
                "#edit-dialog",
                "#edit-notes",
                "#edit-buttons",
                "#menu-dialog",
                "#confirm-dialog",
                "#prompt-dialog",
            )
            if selector not in css
        ]

        self.assertEqual(missing, [])


class KeylineTests(unittest.IsolatedAsyncioTestCase):
    """One line tells the bar from the board."""

    async def test_the_board_is_separated_at_every_bar_level(self) -> None:
        """It is on the board, not the bar — the bar's height changes."""
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            seen = []
            for _ in range(3):
                border = app.query_one("#board").styles.border
                seen.append((app.menu_bar.level, str(border.top[0]), border.top[1].hex.lower()))
                await pilot.press("f2")
                await settle(pilot)

        self.assertEqual([edge for _, edge, _ in seen], ["solid"] * 3)
        self.assertEqual(len({colour for _, _, colour in seen}), 1)

    async def test_only_the_top_edge_is_drawn(self) -> None:
        """A full frame would box the board in; this is a rule, not a border."""
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            border = app.query_one("#board").styles.border

        self.assertEqual(str(border.top[0]), "solid")
        self.assertEqual([str(edge[0]) for edge in (border.right, border.bottom, border.left)], ["", "", ""])


class EdgeStyleTests(unittest.IsolatedAsyncioTestCase):
    """`--edges round|square` swaps every border together."""

    @staticmethod
    def _config(edges: Edges | str) -> BoardConfig:
        """Build the config the way a hand-edited file arrives: as loose text.

        Through ``model_validate`` rather than by assignment, so a value that
        is not one of ours takes the same path it would out of config.json.
        """
        document = config_of("To Do", "Doing", "Done").model_dump(mode="json")
        return BoardConfig.model_validate({**document, "edges": edges})

    async def _borders(self, edges: Edges | str) -> dict[str, str]:
        from pykantui.tui.widgets.card import TaskCard

        backend = JsonBackend(config=self._config(edges))
        backend.create_task(Task(task_id=1, title="Task 01", column_id=1))
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("f2")
            await pilot.press("f2")
            await settle(pilot)

            return {
                "card": str(app.query(TaskCard).first().styles.border.top[0]),
                "column": str(app.query(BoardColumn).first().styles.border.top[0]),
                "field": str(app.menu_bar.query_one("#filter-status").styles.border.top[0]),
            }

    async def test_round_is_the_default(self) -> None:
        borders = await self._borders(Edges.ROUND)

        self.assertEqual(set(borders.values()), {"round"})

    async def test_square_switches_everything_at_once(self) -> None:
        borders = await self._borders(Edges.SQUARE)

        self.assertEqual(set(borders.values()), {"solid"})

    async def test_cards_have_a_full_frame_not_a_left_bar(self) -> None:
        from pykantui.tui.widgets.card import TaskCard

        backend = JsonBackend(config=self._config(Edges.ROUND))
        backend.create_task(Task(task_id=1, title="Task 01", column_id=1))
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            card = app.query(TaskCard).first()
            edges = card.styles.border

        self.assertTrue(all(edge[0] == "round" for edge in (edges.top, edges.right, edges.bottom, edges.left)))

    async def test_the_move_confirmation_is_round_too(self) -> None:
        """It kept a "thick" border — a solid block, which is never round."""
        from pykantui.pages.confirm import ConfirmMoveScreen

        backend = JsonBackend(config=self._config(Edges.ROUND))
        backend.create_task(Task(task_id=1, title="Task 01", column_id=1))
        app = KanbanApp(backend=backend, confirm_moves=True)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, ConfirmMoveScreen)
            dialog = screen.query_one("#confirm-dialog").styles.border.top[0]
            buttons = [str(button.styles.border.top[0]) for button in screen.query("#confirm-buttons Button")]

            await pilot.press("escape")
            await settle(pilot)

        self.assertEqual(str(dialog), "round")
        self.assertEqual(set(buttons), {"round"})

    def test_nothing_is_square_outside_square_mode(self) -> None:
        """A "thick" border anywhere but .edges-square is a missed corner."""
        source = Path(inspect.getfile(KanbanApp)).parent / KanbanApp.CSS_PATH
        stray = [
            block.strip().splitlines()[-1].strip()
            for block in source.read_text(encoding="utf-8").split("border: thick")[:-1]
            if ".edges-square" not in block.rsplit("}", 1)[-1]
        ]

        self.assertEqual(stray, [])

    async def test_an_unknown_value_falls_back_to_round(self) -> None:
        borders = await self._borders("nonsense")

        self.assertEqual(set(borders.values()), {"round"})
