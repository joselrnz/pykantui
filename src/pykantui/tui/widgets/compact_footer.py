"""A small, context-sensitive replacement for Textual's binding dump."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.color import Color, ColorParseError
from textual.containers import Horizontal
from textual.widgets import Label

from pykantui.i18n import translate as _
from pykantui.models import BoardLayout

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class FooterHint(NamedTuple):
    key: str
    action: str


def _key_style(accent: str) -> Style:
    """Convert a Textual theme color into the Rich style used by hint keys.

    Textual accepts names such as ``ansi_green`` while Rich's string parser
    does not.  Parsing through :class:`textual.color.Color` preserves those
    terminal-native colors and gives Rich the normalized color object it
    expects.
    """
    try:
        color = Color.parse(accent).rich_color
    except ColorParseError:
        return Style(bold=True)
    return Style(color=color, bold=True)


class CompactFooter(Horizontal):
    """Show no more than four actions for the current board context."""

    app: KanbanApp
    MAX_HINTS = 4

    def __init__(self) -> None:
        super().__init__(id="compact-footer")
        self._visible_hints: tuple[FooterHint, ...] = ()

    def compose(self) -> ComposeResult:
        for index in range(self.MAX_HINTS):
            yield Label("", id=f"footer-hint-{index}", classes="footer-hint")

    def on_mount(self) -> None:
        self.refresh_context()

    @property
    def visible_hints(self) -> tuple[tuple[str, str], ...]:
        """Plain values for tests and accessibility tooling."""
        return tuple((hint.key, hint.action) for hint in self._visible_hints)

    def refresh_context(self) -> None:
        self._visible_hints = self._context_hints()[: self.MAX_HINTS]
        accent = self.app.current_theme.to_color_system().generate()["accent"]
        key_style = _key_style(accent)
        labels = list(self.query(".footer-hint").results(Label))
        for index, label in enumerate(labels):
            if index >= len(self._visible_hints):
                label.update("")
                label.display = False
                continue
            hint = self._visible_hints[index]
            label.update(Text.assemble((hint.key, key_style), f" {hint.action}"))
            label.display = True

    def _context_hints(self) -> tuple[FooterHint, ...]:
        if self.app.board_layout is not BoardLayout.KANBAN:
            view = self.app.query_one("#work-items-view")
            if bool(getattr(view, "editing", False)):
                return (
                    FooterHint("Ctrl+S", _("Save locally")),
                    FooterHint("Esc", _("Cancel")),
                )
            return (
                FooterHint("e", _("Edit")),
                FooterHint("v", _("Details")),
                FooterHint("Esc", _("Kanban")),
            )

        board = self.app.board
        if board.target_column is not None:
            return (
                FooterHint("H/L", _("Choose column")),
                FooterHint("Enter", _("Move")),
                FooterHint("Esc", _("Cancel")),
            )
        if board.selected is not None:
            return (
                FooterHint("H/L", _("Move")),
                FooterHint("e", _("Edit")),
                FooterHint("v", _("Details")),
                FooterHint(",", _("Column")),
            )
        return (FooterHint("n", _("New card")), FooterHint("/", _("Search")))
