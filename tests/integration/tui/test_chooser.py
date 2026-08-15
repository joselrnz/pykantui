"""Picking one thing from a list.

Replaces two numbered menus in ``kbn init``. The behaviours worth pinning are
the ones a numbered menu did not have: filtering, a description of the
highlighted row *before* committing, and cancelling without picking something
real by mistyping a digit.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from textual.app import App
from textual.widgets import Button, Input, OptionList, Static

from pykantui.config import BoardConfig
from pykantui.pages.chooser import Choice, Chooser
from pykantui.pages.styling import Themed

CHOICES = [
    Choice(value="jira", label="Jira", detail="", description="Atlassian Jira Cloud"),
    Choice(value="linear", label="Linear", detail="", description="Linear teams and states"),
    Choice(value="trello", label="Trello", detail="not tested live", description="Boards and cards"),
    Choice(value="monday", label="Monday.com", detail="", description="Boards and items", keywords=("mon", "dotcom")),
]


class Host(App[str | None]):
    CSS = Chooser.DEFAULT_CSS

    def __init__(self, choices: list[Choice] | None = None) -> None:
        super().__init__()
        self._choices = CHOICES if choices is None else choices

    def on_mount(self) -> None:
        self.push_screen(Chooser(self._choices, title="Which tracker?"), self.exit)


class ThemedHost(Themed, Host):
    def on_mount(self) -> None:
        self.apply_theme()
        super().on_mount()


class ChooserTests(unittest.IsolatedAsyncioTestCase):
    async def test_cyberpunk_styles_the_standalone_chooser_chrome(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"PYKANTUI_HOME": directory}, clear=False),
        ):
            config = BoardConfig.load()
            config.theme = "cyberpunk"
            config.save()
            app = ThemedHost()

            async with app.run_test(size=(100, 38)) as pilot:
                await pilot.pause()
                dialog = app.screen.query_one("#chooser-dialog")
                field = app.screen.query_one("#chooser-filter", Input)
                cancel = app.screen.query_one("#chooser-cancel", Button)
                choose = app.screen.query_one("#chooser-ok", Button)

                self.assertEqual("cyberpunk", app.theme)
                self.assertEqual("#11161d", dialog.styles.background.hex.lower())
                self.assertEqual("#6fb2ff", field.styles.border.top[1].hex.lower())
                self.assertEqual(0.0, cancel.styles.background.a)
                self.assertEqual(0.0, choose.styles.background.a)
                self.assertEqual("#00c8ff", choose.styles.border.top[1].hex.lower())

    async def test_enter_takes_the_highlighted_row(self) -> None:
        app = Host()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual("jira", app.return_value)

    async def test_the_arrows_move_the_highlight(self) -> None:
        app = Host()
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual("linear", app.return_value)

    async def test_escape_returns_nothing(self) -> None:
        app = Host()
        async with app.run_test() as pilot:
            await pilot.press("escape")
            await pilot.pause()

        self.assertIsNone(app.return_value)

    async def test_typing_filters_the_list(self) -> None:
        """The thing a numbered menu cannot do at all."""
        app = Host()
        async with app.run_test() as pilot:
            for key in "trel":
                await pilot.press(key)
            await pilot.pause()
            listing = app.screen.query_one("#chooser-list", OptionList)

            self.assertEqual(1, listing.option_count)

            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual("trello", app.return_value)

    async def test_the_filter_ignores_case(self) -> None:
        app = Host()
        async with app.run_test() as pilot:
            for key in "LIN":
                await pilot.press(key)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual("linear", app.return_value)

    async def test_keywords_match_without_being_shown(self) -> None:
        """ "dotcom" finds Monday.com without cluttering the row."""
        app = Host()
        async with app.run_test() as pilot:
            for key in "dotcom":
                await pilot.press(key)
            await pilot.pause()
            listing = app.screen.query_one("#chooser-list", OptionList)

            self.assertEqual(1, listing.option_count)

    async def test_a_filter_matching_nothing_says_so(self) -> None:
        app = Host()
        async with app.run_test() as pilot:
            for key in "zzzz":
                await pilot.press(key)
            await pilot.pause()
            detail = app.screen.query_one("#chooser-detail", Static)

            self.assertIn("nothing matches", str(detail.content))

    async def test_enter_on_an_empty_filter_result_does_not_choose(self) -> None:
        """Otherwise it would return whatever happened to be first before typing."""
        app = Host()
        async with app.run_test() as pilot:
            for key in "zzzz":
                await pilot.press(key)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            self.assertFalse(app._exit, "it chose something while nothing matched")

    async def test_the_panel_describes_the_highlighted_row(self) -> None:
        """The point of the panel: know what it is before committing."""
        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            detail = app.screen.query_one("#chooser-detail", Static)
            self.assertIn("Atlassian", str(detail.content))

            await pilot.press("down")
            await pilot.pause()
            self.assertIn("Linear", str(detail.content))

    async def test_provider_description_is_literal_text_not_rich_markup(self) -> None:
        hostile = Choice(
            value="remote-1",
            label="Remote",
            description="[bold red]provider text[/] [link=https://example.test]url[/link]",
        )
        app = Host([hostile])

        async with app.run_test() as pilot:
            await pilot.pause()
            detail = app.screen.query_one("#chooser-detail", Static)

            self.assertEqual(hostile.description, str(detail.content))

    async def test_the_detail_column_is_shown(self) -> None:
        """ "not tested live" has to be visible at the moment of choosing."""
        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            listing = app.screen.query_one("#chooser-list", OptionList)
            rendered = " ".join(str(option.prompt) for option in listing._options)

            self.assertIn("not tested live", rendered)

    async def test_one_choice_still_works(self) -> None:
        app = Host([CHOICES[0]])
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual("jira", app.return_value)


class MatchTests(unittest.TestCase):
    def test_an_empty_filter_matches_everything(self) -> None:
        self.assertTrue(CHOICES[0].matches(""))

    def test_the_label_matches(self) -> None:
        self.assertTrue(CHOICES[0].matches("jir"))

    def test_the_description_matches(self) -> None:
        self.assertTrue(CHOICES[0].matches("atlassian"))

    def test_a_keyword_matches(self) -> None:
        self.assertTrue(CHOICES[3].matches("dotcom"))

    def test_something_absent_does_not_match(self) -> None:
        self.assertFalse(CHOICES[0].matches("zzz"))


class EmptyTests(unittest.TestCase):
    def test_choosing_from_nothing_returns_none_without_opening(self) -> None:
        """A modal with no rows is a dead end, so it is never opened."""
        from pykantui.pages.chooser import choose

        self.assertIsNone(choose([]))


class ToneTests(unittest.IsolatedAsyncioTestCase):
    """`tone` is a Rich style, not a Textual CSS variable.

    The default was "primary", which Rich cannot parse. Every caller in the app
    passed a real colour, so the crash only waited for the first one that did
    not -- which is the worst kind of default.
    """

    async def test_the_default_tone_renders(self) -> None:
        plain = [Choice(value="a", label="A"), Choice(value="b", label="B")]
        app = Host(plain)

        async with app.run_test() as pilot:
            await pilot.pause()
            listing = app.screen.query_one("#chooser-list", OptionList)
            self.assertEqual(2, listing.option_count)

            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual("a", app.return_value)

    async def test_every_tone_the_app_uses_renders(self) -> None:
        used = [
            Choice(value="g", label="Verified", marker="●", tone="green", note="verified"),
            Choice(value="y", label="Untested", marker="○", tone="yellow", note="not tested"),
            Choice(value="c", label="Project", marker="▣", tone="cyan"),
        ]
        app = Host(used)

        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(3, app.screen.query_one("#chooser-list", OptionList).option_count)

    async def test_a_label_with_brackets_is_drawn_not_parsed(self) -> None:
        """Project names come from other people; "[Example] Billing" is legal."""
        app = Host([Choice(value="x", label="[Example] Billing System")])

        async with app.run_test() as pilot:
            await pilot.pause()
            listing = app.screen.query_one("#chooser-list", OptionList)
            rendered = str(listing._options[0].prompt)

            self.assertIn("[Example]", rendered)


if __name__ == "__main__":
    unittest.main()
