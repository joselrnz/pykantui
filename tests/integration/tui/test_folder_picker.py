"""Choosing where a workspace goes.

The picker exists because a typed path fails quietly: a typo creates a
workspace somewhere unexpected rather than raising, and you find out two
commands later. So the tests here care most about the ways it could hand back
a path that is wrong or unusable.

Driven through Textual's pilot rather than by calling the actions directly,
so that the bindings and the widget wiring are covered too -- an action nothing
is bound to would otherwise pass every test and do nothing in the app.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast

from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Static

from pykantui.pages.folder import NOISE, FolderPicker, FolderTree, visible
from pykantui.pages.navigation import NavigationAction


class VisibleTests(unittest.TestCase):
    """What the tree offers, and what it leaves out."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make(self, *names: str) -> list[Path]:
        for name in names:
            (self.root / name).mkdir()
        return sorted(self.root.iterdir())

    def test_files_are_never_offered(self) -> None:
        """The answer is a folder, so a file cannot be one."""
        (self.root / "keep").mkdir()
        (self.root / "notes.md").write_text("x", encoding="utf-8")

        names = [path.name for path in visible(self.root.iterdir(), show_hidden=False)]

        self.assertEqual(["keep"], names)

    def test_caches_are_hidden(self) -> None:
        paths = self.make("src", "node_modules", "__pycache__", ".venv")

        names = [path.name for path in visible(paths, show_hidden=False)]

        self.assertEqual(["src"], names)

    def test_hidden_directories_appear_when_asked(self) -> None:
        paths = self.make("src", ".config")

        shown = [path.name for path in visible(paths, show_hidden=True)]

        self.assertEqual([".config", "src"], shown)

    def test_noise_stays_hidden_even_with_hidden_shown(self) -> None:
        """`.` reveals dotfiles, not build output -- that is never wanted."""
        paths = self.make("src", ".config", "node_modules")

        shown = [path.name for path in visible(paths, show_hidden=True)]

        self.assertNotIn("node_modules", shown)
        self.assertIn(".config", shown)

    def test_sorted_case_insensitively(self) -> None:
        paths = self.make("Zebra", "apple", "Banana")

        names = [path.name for path in visible(paths, show_hidden=False)]

        self.assertEqual(["apple", "Banana", "Zebra"], names)

    def test_an_unreadable_entry_does_not_break_the_listing(self) -> None:
        """One unstattable entry in a home directory must not empty the tree."""

        class Exploding(Path):
            _flavour = type(Path())._flavour  # type: ignore[attr-defined]

            def is_dir(self, *args: object, **kwargs: object) -> bool:
                raise OSError("no")

        paths = [*self.make("good"), Exploding(self.root / "bad")]

        names = [path.name for path in visible(paths, show_hidden=False)]

        self.assertEqual(["good"], names)


class PickerHost(App[Path | NavigationAction | None]):
    """Minimal host, so the picker is exercised the way init runs it."""

    CSS = FolderPicker.DEFAULT_CSS

    def __init__(self, start: Path) -> None:
        super().__init__()
        self._start = start

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(FolderPicker(self._start), self.exit)


class PickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "projects").mkdir()
        (self.root / "node_modules").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_ctrl_enter_chooses_where_you_are(self) -> None:
        """Confirmation is separate from opening the highlighted directory."""
        app = PickerHost(self.root)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+enter")
            await pilot.pause()

        self.assertEqual(self.root, app.return_value)

    async def test_enter_opens_the_highlighted_directory_without_choosing(self) -> None:
        app = PickerHost(self.root)
        target = self.root / "projects"

        async with app.run_test() as pilot:
            await pilot.pause()
            tree = app.screen.query_one("#folder-tree", FolderTree)
            tree.root.expand()
            await pilot.pause()
            for line, node in enumerate(tree._tree_lines):
                data = getattr(node.node, "data", None)
                if data is not None and Path(data.path) == target:
                    tree.cursor_line = line
                    break
            else:
                self.fail("projects was not rendered in the directory tree")

            await pilot.press("enter")
            await pilot.pause()

            self.assertIsNone(app.return_value)
            self.assertEqual(target, cast(FolderPicker, app.screen).chosen)

    async def test_navigation_buttons_are_visible_and_new_folder_has_no_key_suffix(self) -> None:
        app = PickerHost(self.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            labels = {
                button.id: str(button.label)
                for button in app.screen.query("#folder-navigation Button, #folder-buttons Button").results(Button)
            }

        self.assertEqual("↑ Parent", labels["folder-parent"])
        self.assertEqual("⌂ Home", labels["folder-home"])
        self.assertEqual("/ Path…", labels["folder-go-to"])
        self.assertEqual("+ New folder", labels["folder-new-folder"])
        self.assertNotIn("^N", " ".join(labels.values()))
        for glyph in "↑⌂/+/":
            with self.subTest(glyph=glyph):
                self.assertEqual(1, cell_len(glyph))

    async def test_parent_button_goes_up_one_directory(self) -> None:
        app = PickerHost(self.root / "projects")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#folder-parent")
            await pilot.pause()
            await pilot.press("ctrl+enter")
            await pilot.pause()

        self.assertEqual(self.root, app.return_value)

    async def test_path_button_opens_the_typed_navigation_prompt(self) -> None:
        app = PickerHost(self.root)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#folder-go-to")
            await pilot.pause()

            field = app.screen.query_one("#folder-input", Input)
            self.assertTrue(app.screen.has_class("-asking"))
            self.assertTrue(field.has_focus)
            self.assertEqual(str(self.root), field.value)

    async def test_escape_returns_nothing(self) -> None:
        app = PickerHost(self.root)
        async with app.run_test() as pilot:
            await pilot.press("escape")
            await pilot.pause()

        self.assertIsNone(app.return_value)

    async def test_backspace_goes_up(self) -> None:
        start = self.root / "projects"
        app = PickerHost(start)
        async with app.run_test() as pilot:
            await pilot.press("backspace")
            await pilot.pause()
            await pilot.press("ctrl+enter")
            await pilot.pause()

        self.assertEqual(self.root, app.return_value)

    async def test_a_new_folder_is_created_and_becomes_the_choice(self) -> None:
        app = PickerHost(self.root)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+n")
            await pilot.pause()
            for key in "boards":
                await pilot.press(key)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+enter")
            await pilot.pause()

        made = self.root / "boards"
        self.assertTrue(made.is_dir(), "the folder was not created")
        self.assertEqual(made, app.return_value)

    async def test_a_duplicate_folder_name_is_reported_not_raised(self) -> None:
        app = PickerHost(self.root)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+n")
            await pilot.pause()
            for key in "projects":  # already exists
                await pilot.press(key)
            await pilot.press("enter")
            await pilot.pause()
            message = app.screen.query_one("#folder-error", Static).content

        self.assertIn("already exists", str(message))

    async def test_toggling_hidden_reloads_the_tree(self) -> None:
        app = PickerHost(self.root)
        async with app.run_test() as pilot:
            tree = app.screen.query_one("#folder-tree", FolderTree)
            self.assertFalse(tree.show_hidden)
            await pilot.press("full_stop")
            await pilot.pause()

            self.assertTrue(tree.show_hidden)

    async def test_an_unwritable_directory_is_refused_with_the_picker_still_open(self) -> None:
        """Better to say so while another folder is one keypress away."""
        app = PickerHost(self.root)
        async with app.run_test() as pilot:
            screen = cast(FolderPicker, app.screen)
            screen.chosen = self.root / "does-not-exist"
            await pilot.press("ctrl+enter")
            await pilot.pause()

            self.assertIsNone(app.return_value, "it dismissed with an unusable path")
            message = app.screen.query_one("#folder-error", Static).content
            self.assertIn("cannot write", str(message))


class UsableTests(unittest.TestCase):
    """Where the picker opens when the requested directory is not there."""

    def test_it_falls_back_to_the_nearest_real_parent(self) -> None:
        from pykantui.pages.folder import _usable

        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            missing = root / "gone" / "further" / "still-gone"

            self.assertEqual(root, _usable(missing))

    def test_a_real_directory_is_returned_as_is(self) -> None:
        from pykantui.pages.folder import _usable

        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()

            self.assertEqual(root, _usable(root))


class NoiseTests(unittest.TestCase):
    def test_the_list_covers_the_usual_suspects(self) -> None:
        for name in (".git", "node_modules", "__pycache__", ".venv"):
            self.assertIn(name, NOISE)

    def test_it_does_not_hide_ordinary_project_names(self) -> None:
        for name in ("src", "docs", "projects", "boards"):
            self.assertNotIn(name, NOISE)


class CanRunTests(unittest.TestCase):
    def test_no_terminal_means_no_picker(self) -> None:
        """A piped run gets the typed prompt, not a Textual app nobody sees."""
        import io
        import sys
        from unittest import mock

        from pykantui.pages import folder

        with mock.patch.object(sys, "stdin", io.StringIO()):
            self.assertFalse(folder.can_run())


class InitWiringTests(unittest.TestCase):
    """`kbn init` uses it, but never at the cost of the scriptable path."""

    def test_an_explicit_path_skips_the_browser(self) -> None:
        from unittest import mock

        from pykantui.commands.init import _pick_path

        with tempfile.TemporaryDirectory() as name, mock.patch("pykantui.pages.folder.choose") as browser:
            supplied = Path(name)

            found = _pick_path(supplied, "JPT", interactive=True)

            self.assertEqual(supplied.resolve(), found)
            browser.assert_not_called()

    def test_no_browse_falls_back_to_typing(self) -> None:
        from unittest import mock

        from pykantui.commands.init import _pick_path

        with mock.patch("pykantui.pages.folder.choose") as browser, mock.patch("builtins.input", return_value=""):
            found = _pick_path(None, "JPT", interactive=True, browse=False)

            browser.assert_not_called()
            self.assertEqual(Path.cwd() / "jpt", found)

    def test_the_workspace_is_named_under_the_chosen_folder(self) -> None:
        """The picker answers with a parent; the workspace keeps its own name.

        Otherwise two inits into the same folder would land on top of one
        another, and the second would look like a corrupted first.
        """
        from unittest import mock

        from pykantui.commands.init import _pick_path

        with tempfile.TemporaryDirectory() as name:
            chosen = Path(name).resolve()
            with (
                mock.patch("pykantui.pages.folder.can_run", return_value=True),
                mock.patch("pykantui.pages.folder.choose", return_value=chosen),
            ):
                found = _pick_path(None, "JPT", interactive=True)

            self.assertEqual(chosen / "jpt", found)

    def test_cancelling_the_browser_falls_back_to_typing(self) -> None:
        from unittest import mock

        from pykantui.commands.init import _pick_path

        with tempfile.TemporaryDirectory() as name:
            typed = Path(name).resolve()
            with (
                mock.patch("pykantui.pages.folder.can_run", return_value=True),
                mock.patch("pykantui.pages.folder.choose", return_value=None),
                mock.patch("builtins.input", return_value=str(typed)),
            ):
                found = _pick_path(None, "JPT", interactive=True)

            self.assertEqual(typed, found)

    def test_a_non_interactive_run_still_demands_a_path(self) -> None:
        from pykantui.commands.init import _pick_path
        from pykantui.tracker import ProviderError

        with self.assertRaises(ProviderError):
            _pick_path(None, "JPT", interactive=False)


if __name__ == "__main__":
    unittest.main()
