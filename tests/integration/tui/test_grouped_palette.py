"""The wide command palette stays searchable while grouping related actions."""

from __future__ import annotations

import unittest

from rich.cells import cell_len
from textual.widgets import Input, Static

from pykantui.models import BoardLayout
from pykantui.pages.grouped_palette import GroupedCommandPalette
from pykantui.tui.glyphs import SEARCH_GLYPH
from tests.integration.tui.test_menu_bar import SIZE, SyncProviderMenuBackend, make_app, settle


class GroupedPaletteTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_a_one_cell_semantic_icon_in_every_theme(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+p")
            await pilot.pause()
            icon = app.screen.query_one("#grouped-palette-search-icon", Static)

            self.assertEqual("⌕", SEARCH_GLYPH)
            self.assertEqual(1, cell_len(SEARCH_GLYPH))
            self.assertEqual(SEARCH_GLYPH, str(icon.render()))
            for theme in sorted(app.available_themes):
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.pause()
                    self.assertEqual(
                        app.theme_variables["accent"],
                        icon.styles.color.hex,
                    )

    async def test_header_opens_the_wide_grouped_palette(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()

            screen = app.screen
            self.assertIsInstance(screen, GroupedCommandPalette)
            assert isinstance(screen, GroupedCommandPalette)
            self.assertEqual(
                ("Board", "Organize", "Sync with Jira", "Help", "System"),
                screen.visible_labels,
            )
            self.assertEqual("--grouped-command-palette", screen.id)

    async def test_ctrl_p_opens_the_same_grouped_palette(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+p")
            await pilot.pause()

            self.assertIsInstance(app.screen, GroupedCommandPalette)

    async def test_enter_expands_organize_inside_the_same_screen(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            await pilot.press("down", "enter")
            await pilot.pause()

            self.assertIs(screen, app.screen)
            self.assertEqual(
                ("Board", "Organize", "Filter", "Sort", "Columns", "View", "Sync with Jira", "Help", "System"),
                screen.visible_labels,
            )

    async def test_view_layouts_keep_their_distinct_labels(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            await pilot.press("down", "enter")
            await pilot.press("down", "down", "down", "down", "enter")
            await pilot.press("down", "enter")
            await pilot.pause()

            self.assertIn("▥ Kanban", screen.visible_labels)
            self.assertIn("▦ Split", screen.visible_labels)
            self.assertIn("▤ Rows", screen.visible_labels)
            self.assertNotIn("Home · ▥ Kanban", screen.visible_labels)

    async def test_right_expands_and_left_collapses_the_selected_group(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            await pilot.press("down", "right")
            await pilot.pause()
            self.assertIn("Filter", screen.visible_labels)

            await pilot.press("left")
            await pilot.pause()
            self.assertNotIn("Filter", screen.visible_labels)

    async def test_filter_uses_compact_nested_provider_aware_groups(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            await pilot.press("down", "enter", "down", "enter")
            await pilot.pause()

            self.assertIn("State", screen.visible_labels)
            self.assertIn("Provider fields", screen.visible_labels)
            self.assertNotIn("Blocked", screen.visible_labels)

            await pilot.press("down", "enter")
            await pilot.pause()

            self.assertIn("Blocked", screen.visible_labels)
            self.assertIn("Due today", screen.visible_labels)

    async def test_search_finds_provider_commands_inside_collapsed_groups(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            screen.query_one("#grouped-palette-search", Input).value = "assignee"
            await pilot.pause()

            self.assertEqual("Organize · Filter · Provider fields · Assignee…", screen.visible_labels[0])

    async def test_search_keeps_the_original_fuzzy_matching_behavior(self) -> None:
        app = make_app(SyncProviderMenuBackend("jira"))

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            screen.query_one("#grouped-palette-search", Input).value = "asgn"
            await pilot.pause()

            self.assertEqual("Organize · Filter · Provider fields · Assignee…", screen.visible_labels[0])

    async def test_selecting_a_search_result_runs_it_after_closing(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            screen.query_one("#grouped-palette-search", Input).value = "home kanban"
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)

            self.assertEqual(BoardLayout.KANBAN, app.board_layout)
            self.assertNotIsInstance(app.screen, GroupedCommandPalette)

    async def test_local_board_omits_provider_only_search_results_and_sync(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#app-header-menu")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GroupedCommandPalette)

            self.assertNotIn("Sync with provider", " ".join(screen.visible_labels))
            screen.query_one("#grouped-palette-search", Input).value = "assignee"
            await pilot.pause()

            # Assignee is now a provider-neutral sortable Rows/Split column,
            # so local boards expose only that sort command. They still must
            # not promise the provider-only Assignee filter.
            self.assertEqual(("Organize · Sort · Assignee",), screen.visible_labels)
            self.assertNotIn("Provider fields", " ".join(screen.visible_labels))

            screen.query_one("#grouped-palette-search", Input).value = ""
            await pilot.pause()
            await pilot.press("down", "enter", "down", "enter")
            await pilot.pause()

            self.assertNotIn("Provider fields", screen.visible_labels)

    async def test_escape_closes_the_whole_palette(self) -> None:
        app = make_app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            original = app.screen
            await pilot.click("#app-header-menu")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            self.assertIs(original, app.screen)


if __name__ == "__main__":
    unittest.main()
