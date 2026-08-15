"""Choosing a directory, by browsing rather than by typing a path.

``kbn init`` needs somewhere to put a workspace, and typing an absolute path
from memory is the step people get wrong -- a typo makes a new directory
somewhere unexpected rather than an error, and you find out later.

Written against Textual's own :class:`~textual.widgets.DirectoryTree`. It is
deliberately *not* a port of the file explorer in pypanemux: that project and
its upstream are AGPL-3.0-or-later, and pykantui is MIT. The behaviours here --
hiding caches, ``.`` to toggle hidden entries -- are ordinary ideas, and the
code is new.

Two things separate this from a file browser:

**Directories only.** The answer is a folder, so files are not shown at all.
Listing them would invite selecting one, which cannot be the answer.

**Selection is where you are, not what you clicked.** The highlighted directory
*is* the choice, so opening a folder and pressing enter selects it. A picker
that made you highlight a folder from its parent could not select the root you
started in.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Label, Static

from pykantui.i18n import translate as _
from pykantui.pages.navigation import NavigationAction
from pykantui.pages.styling import DIALOG_CSS, Themed

#: Directories that are never worth showing when choosing where to put files.
#: Not a security measure -- just noise removal on a machine with real projects
#: on it. ``.`` still reveals dotted entries, and nothing here is hidden from a
#: path typed into the box.
NOISE = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "target",
        "build",
        "dist",
        "site-packages",
    }
)


def visible(paths: Iterable[Path], *, show_hidden: bool) -> list[Path]:
    """Directories worth offering, in name order.

    Unreadable entries are dropped rather than raised on: a home directory
    routinely contains something the current user cannot stat, and one of those
    must not take the whole picker down.
    """
    kept: list[Path] = []
    for path in paths:
        try:
            if not path.is_dir():
                continue
        except OSError:
            continue
        if path.name in NOISE:
            continue
        if not show_hidden and path.name.startswith("."):
            continue
        kept.append(path)
    return sorted(kept, key=lambda item: item.name.casefold())


class FolderTree(DirectoryTree):
    """A directory tree with the files left out."""

    def __init__(self, path: str | Path, *, show_hidden: bool = False, **kwargs: object) -> None:
        self.show_hidden = show_hidden
        super().__init__(path, **kwargs)  # type: ignore[arg-type]

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return visible(paths, show_hidden=self.show_hidden)


class FolderPicker(ModalScreen[Path | NavigationAction | None]):
    """Pick a directory. Returns the chosen path, or ``None`` if cancelled."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+b", "back", "back", show=False),
        Binding("ctrl+enter", "choose", "choose folder", priority=True),
        Binding("full_stop", "toggle_hidden", "hidden"),
        Binding("ctrl+n", "new_folder", "new folder", show=False),
        Binding("ctrl+l", "go_to", "type a path"),
        Binding("backspace", "go_up", "up"),
        Binding("ctrl+h", "go_home", "home"),
    ]

    DEFAULT_CSS = (
        DIALOG_CSS
        + """
    FolderPicker { align: center middle; }
    FolderPicker #folder-dialog {
        height: 34;
        border: round $primary;
    }
    FolderPicker #folder-chosen { color: $accent; }

    FolderPicker #folder-navigation {
        width: 100%;
        height: 3;
        align-horizontal: left;
        margin-top: 1;
    }
    FolderPicker #folder-navigation Button {
        width: auto;
        min-width: 14;
        height: 3;
        margin-right: 1;
        border: round $border-blurred;
        background: transparent;
        color: $text-muted;
    }
    FolderPicker #folder-navigation Button:hover,
    FolderPicker #folder-navigation Button:focus {
        border: round $accent;
        color: $accent;
        text-style: bold;
    }

    FolderPicker #folder-tree {
        margin-top: 0;
        border: round $border-blurred;
        background: $panel;
    }
    FolderPicker #folder-tree:focus {
        border: round $accent;
    }
    FolderPicker #folder-tree > .tree--cursor {
        background: $primary;
        color: $text;
    }

    /* The prompt row sits *under* the tree and never replaces it: naming a
       folder is exactly when you need to see where it is going. */
    FolderPicker #folder-prompt { display: none; height: auto; margin-bottom: 1; }
    FolderPicker.-asking #folder-prompt { display: block; }
    FolderPicker #folder-prompt-label { color: $text-muted; }
    FolderPicker #folder-input { border: tall $accent-lighten-1; }

    FolderPicker #folder-buttons Button,
    FolderPicker #folder-buttons Button.-primary {
        min-width: 16;
        height: 3;
        border: round $border-blurred;
        background: transparent;
        color: $text-muted;
        text-style: none;
    }
    FolderPicker #folder-new-folder {
        border: round $primary;
        color: $text-primary;
    }
    FolderPicker #folder-ok,
    FolderPicker #folder-ok.-primary {
        border: round $accent;
        color: $accent;
        text-style: bold;
    }
    FolderPicker #folder-buttons Button:hover,
    FolderPicker #folder-buttons Button:focus,
    FolderPicker #folder-buttons Button.-primary:hover,
    FolderPicker #folder-buttons Button.-primary:focus {
        border: round $accent;
        background: transparent;
        color: $accent;
        text-style: bold;
    }
    """
    )

    def __init__(
        self,
        start: Path | None = None,
        *,
        title: str | None = None,
        allow_back: bool = False,
    ) -> None:
        super().__init__()
        self._title = title or _("Where should it live?")
        self._start = _usable(start or Path.cwd())
        self.chosen: Path = self._start
        self._allow_back = allow_back

        #: Which question the prompt row is currently asking, if any.
        self._mode = ""

    def compose(self) -> ComposeResult:
        back_label = _("Back")
        yield Header()
        with Vertical(id="folder-dialog", classes="pk-dialog"):
            yield Label(self._title, id="folder-title", classes="pk-title")
            yield Static(str(self._start), id="folder-chosen", classes="pk-path")
            with Horizontal(id="folder-navigation"):
                yield Button(_("↑ Parent"), id="folder-parent")
                yield Button(_("⌂ Home"), id="folder-home")
                yield Button(_("/ Path…"), id="folder-go-to")
            yield FolderTree(self._start, id="folder-tree", classes="pk-panel")

            with Vertical(id="folder-prompt"):
                yield Static("", id="folder-prompt-label")
                yield Input(id="folder-input")

            yield Static("", id="folder-error", classes="pk-error")
            yield Static(
                _("↑↓ move · enter open · backspace parent · . hidden"),
                id="folder-help",
                classes="pk-help",
            )
            with Horizontal(id="folder-buttons", classes="pk-buttons"):
                if self._allow_back:
                    yield Button(f"← {back_label}", id="folder-back")
                yield Button(_("Cancel"), id="folder-cancel")
                yield Button(f"+ {_('New folder')}", id="folder-new-folder")
                yield Button(_("Choose folder"), variant="primary", id="folder-ok")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#folder-tree", FolderTree).focus()

    # ---- what is currently selected -------------------------------------

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        """Track the highlighted directory without closing.

        Textual sends this when a directory is opened, which is exactly the
        moment the choice should follow the cursor -- so browsing into a folder
        makes it the answer, and enter confirms it.
        """
        event.stop()
        self._set_chosen(event.path)

    def on_tree_node_highlighted(self, event: events.Event) -> None:
        path = getattr(getattr(event, "node", None), "data", None)
        if path is not None and getattr(path, "path", None) is not None:
            self._set_chosen(Path(path.path))

    def _set_chosen(self, path: Path) -> None:
        self.chosen = path
        self.query_one("#folder-chosen", Static).update(str(path))
        self.query_one("#folder-error", Static).update("")

    # ---- actions ---------------------------------------------------------

    def action_choose(self) -> None:
        if self.has_class("-asking"):
            self._submit()
            return
        if not _writable(self.chosen):
            self.query_one("#folder-error", Static).update(f"cannot write to {self.chosen} — pick somewhere else")
            return
        self.dismiss(self.chosen)

    def action_cancel(self) -> None:
        """Escape backs out of the prompt first, and only then out of the dialog."""
        if self.has_class("-asking"):
            self._stop_asking()
            return
        self.dismiss(None)

    def action_back(self) -> None:
        """Leave the picker for the previous wizard step, not its parent path."""
        if self._allow_back:
            self.dismiss(NavigationAction.BACK)

    def action_toggle_hidden(self) -> None:
        tree = self.query_one("#folder-tree", FolderTree)
        tree.show_hidden = not tree.show_hidden
        tree.reload()

    def action_go_up(self) -> None:
        self._reroot(self.chosen.parent)

    def action_go_home(self) -> None:
        self._reroot(Path.home())

    def action_new_folder(self) -> None:
        self._ask("new-folder", f"New folder inside {self.chosen}", "")

    def action_go_to(self) -> None:
        """Type or paste a path, for when browsing to it would take all day."""
        self._ask("go-to", "Go to path (~ works)", str(self.chosen))

    def _ask(self, mode: str, label: str, value: str) -> None:
        self._mode = mode
        self.add_class("-asking")
        self.query_one("#folder-prompt-label", Static).update(label)
        field = self.query_one("#folder-input", Input)
        field.value = value
        field.focus()

    def _stop_asking(self) -> None:
        self._mode = ""
        self.remove_class("-asking")
        self.query_one("#folder-error", Static).update("")
        self.query_one("#folder-tree", FolderTree).focus()

    def _submit(self) -> None:
        if self._mode == "new-folder":
            self._create_folder()
        elif self._mode == "go-to":
            self._go_to_typed()

    def _go_to_typed(self) -> None:
        typed = self.query_one("#folder-input", Input).value.strip()
        if not typed:
            self._stop_asking()
            return

        target = Path(typed).expanduser()
        if not target.is_dir():
            self.query_one("#folder-error", Static).update(f"no such directory: {typed}")
            return
        self._stop_asking()
        self._reroot(target)

    def _create_folder(self) -> None:
        """Make a directory under the current selection and move into it.

        Offered because the answer to "where should it live?" is usually a
        folder that does not exist yet, and being sent away to a shell to run
        one ``mkdir`` is the kind of dead end that makes a picker not worth
        opening.
        """
        name = self.query_one("#folder-input", Input).value.strip()
        error = self.query_one("#folder-error", Static)
        if not name:
            self._stop_asking()
            return

        target = self.chosen / name
        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            error.update(f"{name} already exists")
            return
        except OSError as failure:
            error.update(f"could not create it: {failure.strerror or failure}")
            return

        self._stop_asking()
        self._reroot(target)

    def _reroot(self, path: Path) -> None:
        """Show ``path`` in context, and make it the current choice.

        The tree is rooted at the *parent* so the target appears alongside its
        siblings. Rooting onto the target itself showed a single row with
        nothing around it -- technically correct and useless to navigate from.
        """
        target = _usable(path)
        root = target.parent if target.parent != target and target.parent.is_dir() else target

        tree = self.query_one("#folder-tree", FolderTree)
        tree.path = root
        self._set_chosen(target)
        tree.focus()

        if root != target:
            # Put the cursor on the folder we just moved to, once the tree has
            # had a chance to load the directory.
            self.call_after_refresh(self._highlight, target)

    def _highlight(self, target: Path) -> None:
        """Move the cursor onto ``target`` in the tree, if it is visible yet.

        Best effort by design: a slow or unreadable directory should leave the
        cursor where it is rather than raise in a callback nobody can catch.
        """
        tree = self.query_one("#folder-tree", FolderTree)
        try:
            tree.root.expand()
            for line, node in enumerate(tree._tree_lines):
                data = getattr(node.node, "data", None)
                if data is not None and Path(getattr(data, "path", "")) == target:
                    tree.cursor_line = line
                    return
        except Exception:  # noqa: BLE001 - the cursor is a nicety, not a promise
            return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "folder-parent":
            self.action_go_up()
        elif event.button.id == "folder-back":
            self.action_back()
        elif event.button.id == "folder-home":
            self.action_go_home()
        elif event.button.id == "folder-go-to":
            self.action_go_to()
        elif event.button.id == "folder-new-folder":
            self.action_new_folder()
        elif event.button.id == "folder-ok":
            self.action_choose()
        else:
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()


def _usable(path: Path) -> Path:
    """The nearest directory at or above ``path`` that actually exists.

    A remembered path whose drive has been unplugged, or a home directory that
    is not there, should open the picker somewhere sensible rather than crash
    it before it is visible.
    """
    try:
        candidate = path.expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        candidate = Path.cwd()

    for step in (candidate, *candidate.parents):
        if step.is_dir():
            return step
    return Path(candidate.anchor or Path.cwd())


def _writable(path: Path) -> bool:
    """Whether a workspace could actually be created here.

    Checked before dismissing rather than after, so "permission denied" arrives
    while the picker is still open and another folder is one keypress away.
    """
    try:
        if not path.is_dir():
            return False
    except OSError:
        return False
    return os.access(path, os.W_OK)


def can_run() -> bool:
    """Whether a modal picker makes sense here.

    False when there is no terminal to draw on -- a piped or redirected run
    gets the typed prompt instead, rather than a Textual app that would try to
    take over a stream nobody is watching.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def choose(start: Path | None = None, *, title: str = "Where should it live?") -> Path | None:
    """Open the picker on its own and return the chosen directory.

    A standalone Textual app rather than a screen pushed onto an existing one,
    because ``kbn init`` is a plain terminal wizard with no app running. The
    picker takes over the terminal for as long as it is open and hands it back
    on the way out, so the wizard carries on printing where it left off.
    """
    from textual.app import App  # noqa: PLC0415 - keeps the CLI's fast path light

    class PickerApp(Themed, App[Path | None]):
        CSS = FolderPicker.DEFAULT_CSS
        TITLE = "pykantui"
        SUB_TITLE = "choose a folder"

        def on_mount(self) -> None:
            self.apply_theme()
            self.push_screen(FolderPicker(start, title=title), self._finish)

        def _finish(self, result: Path | NavigationAction | None) -> None:
            """Normalize the wizard-only Back result for the standalone picker."""
            self.exit(None if result is NavigationAction.BACK else result)

    return PickerApp().run(inline=False)
