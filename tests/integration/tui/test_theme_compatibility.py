"""Every selectable Textual theme must keep the mounted TUI usable."""

from __future__ import annotations

import unittest

from rich.style import Style
from rich.text import Text
from textual.color import Color
from textual.widgets import Label

from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.compact_footer import CompactFooter
from tests.integration.tui.test_board_tui import workflow_backend


class ThemeCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def assert_footer_uses_theme_accent(
        self,
        app: KanbanApp,
        footer: CompactFooter,
    ) -> None:
        """The visible key stays bold and uses the normalized theme accent."""
        content = footer.query(Label).first().content
        self.assertIsInstance(content, Text)
        assert isinstance(content, Text)
        self.assertTrue(content.spans)
        style = content.spans[0].style
        self.assertIsInstance(style, Style)
        assert isinstance(style, Style)
        expected = Color.parse(
            app.current_theme.to_color_system().generate()["accent"]
        ).rich_color
        self.assertTrue(style.bold)
        self.assertEqual(expected, style.color)

    async def test_ansi_themes_can_be_used_on_cold_start(self) -> None:
        for theme in ("ansi-dark", "ansi-light"):
            with self.subTest(theme=theme):
                backend = workflow_backend()
                config = backend.board_config()
                assert config is not None
                config.theme = theme
                app = KanbanApp(backend, confirm_moves=False)

                async with app.run_test(size=(120, 30)) as pilot:
                    await pilot.pause()
                    footer = app.query_one(CompactFooter)

                    self.assertEqual(theme, app.theme)
                    self.assertTrue(footer.visible_hints)
                    self.assertTrue(str(footer.query(Label).first().content))
                    self.assert_footer_uses_theme_accent(app, footer)

    async def test_footer_survives_switching_through_every_available_theme(self) -> None:
        app = KanbanApp(workflow_backend(), confirm_moves=False)

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            available = tuple(sorted(app.available_themes))
            footer = app.query_one(CompactFooter)

            for theme in available:
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.pause()

                    self.assertEqual(theme, app.theme)
                    self.assertTrue(footer.visible_hints)
                    self.assertTrue(str(footer.query(Label).first().content))
                    self.assert_footer_uses_theme_accent(app, footer)

        self.assertIn("ansi-dark", available)
        self.assertIn("ansi-light", available)


if __name__ == "__main__":
    unittest.main()
