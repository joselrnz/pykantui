"""Choosing one thing from a list, without typing its number.

``kbn init`` asked two questions as numbered menus: which tracker, then which
project. A numbered menu is fine for five items and poor for fifty -- you
cannot search it, you cannot see what you are choosing until you have chosen,
and a mistyped digit picks something real.

This is the same shape as the folder picker: a list you move through with the
arrows, a filter you type into, and a panel that describes whatever is under
the cursor before you commit to it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from pykantui.i18n import translate as _
from pykantui.pages.navigation import NavigationAction
from pykantui.pages.styling import DIALOG_CSS, Themed


@dataclass(frozen=True)
class Choice:
    """One row: what it is, what to show, and what to say about it."""

    value: str
    label: str

    #: Shown to the right of the label, dimmed. A key, an id, a count.
    detail: str = ""

    #: Shown in the panel below when this row is under the cursor.
    description: str = ""

    #: Extra words that should match the filter without being displayed.
    keywords: tuple[str, ...] = field(default=())

    #: A glyph in the left gutter, and the colour it is drawn in. Colour is
    #: used to mean something here -- ready versus untested -- rather than to
    #: decorate: a row you can trust and a row you cannot should not look the
    #: same at a glance.
    #:
    #: ``tone`` is a **Rich** style name -- "green", "yellow", "cyan" -- not a
    #: Textual CSS variable. These rows are Rich ``Text``, and Rich raises
    #: MissingStyle on "$primary". The default was "primary", which worked
    #: wherever a caller passed a real colour and crashed wherever one did not.
    marker: str = "\u25cf"
    tone: str = "cyan"

    #: Short state, right-aligned and dimmed. Long enough to be read, short
    #: enough not to compete with the label.
    note: str = ""

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        haystack = " ".join((self.label, self.detail, self.description, *self.keywords))
        return needle.casefold() in haystack.casefold()


class Chooser(ModalScreen[str | NavigationAction | None]):
    """Pick one value. Returns it, or ``None`` if cancelled."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+b", "back", "back", show=False),
        Binding("enter", "choose", "choose", priority=True),
        Binding("ctrl+c", "cancel", "cancel"),
    ]

    DEFAULT_CSS = (
        DIALOG_CSS
        + """
    Chooser { align: center middle; }
    Chooser > #chooser-dialog { height: 28; }
    Chooser #chooser-filter {
        margin: 1 0 0 0;
        border: round $border;
        background: $background;
    }
    Chooser #chooser-filter:focus { border: round $accent-lighten-3; }

    Chooser #chooser-ok,
    Chooser #chooser-ok.-primary {
        border: round $accent;
        background: transparent;
        color: $accent;
        text-style: bold;
    }

    /* The one saturated thing on screen: where you are. */
    Chooser #chooser-list > .option-list--option-highlighted {
        background: $accent 25%;
        text-style: bold;
    }
    Chooser #chooser-list > .option-list--option-hover { background: $accent 12%; }

    /* Says what the highlighted row *is* before you commit to it, which a
       numbered menu cannot do. */
    Chooser #chooser-detail {
        height: 4;
        color: $text-muted;
        border-left: outer $primary;
        padding: 0 0 0 1;
    }
    """
    )

    def __init__(
        self,
        choices: list[Choice],
        *,
        title: str | None = None,
        filter_hint: str | None = None,
        allow_back: bool = False,
    ) -> None:
        super().__init__()
        self._all = list(choices)
        self._shown = list(choices)
        self._title = title or _("Choose one")
        self._hint = filter_hint or _("type to filter")
        self._allow_back = allow_back

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="chooser-dialog", classes="pk-dialog"):
            yield Label(self._title, id="chooser-title", classes="pk-title")
            yield Input(placeholder=self._hint, id="chooser-filter")
            yield OptionList(id="chooser-list", classes="pk-panel")
            # Provider descriptions, URLs, and names are untrusted text. A
            # literal Static prevents Rich/Textual markup and control-like
            # bracket sequences from being interpreted by the terminal UI.
            yield Static("", id="chooser-detail", markup=False)
            back_label = _("Back")
            help_text = _("↑↓ move · type to filter · enter choose · esc cancel")
            if self._allow_back:
                help_text = f"{help_text} · ctrl+b {back_label.casefold()}"
            yield Static(
                help_text,
                id="chooser-help",
                classes="pk-help",
            )
            with Horizontal(id="chooser-buttons", classes="pk-buttons"):
                if self._allow_back:
                    yield Button(f"← {back_label}", id="chooser-back")
                yield Button(_("Cancel"), id="chooser-cancel")
                yield Button(_("Choose"), variant="primary", id="chooser-ok")
        yield Footer()

    def on_mount(self) -> None:
        self._fill()
        # Focus the list, not the filter: most lists are short enough to arrow
        # through, and typing still reaches the filter via on_key below.
        self.query_one("#chooser-list", OptionList).focus()

    # ---- the list --------------------------------------------------------

    def _fill(self) -> None:
        listing = self.query_one("#chooser-list", OptionList)
        listing.clear_options()

        label_w = max((len(choice.label) for choice in self._shown), default=0)
        detail_w = max((len(choice.detail) for choice in self._shown), default=0)

        for choice in self._shown:
            listing.add_option(Option(self._row(choice, label_w, detail_w), id=choice.value))

        if self._shown:
            listing.highlighted = 0
            self._describe(0)
        else:
            self.query_one("#chooser-detail", Static).update("nothing matches that filter")

    def _row(self, choice: Choice, label_w: int, detail_w: int) -> Text:
        """One line: marker, label, identifier, state.

        Built as a :class:`~rich.text.Text` rather than markup so a label
        containing square brackets is drawn, not parsed -- tracker and project
        names come from other people.
        """
        row = Text(no_wrap=True)
        row.append(f" {choice.marker}  ", style=choice.tone)
        row.append(choice.label.ljust(label_w), style="bold")
        if detail_w:
            row.append("   ")
            row.append(choice.detail.ljust(detail_w), style="dim")
        if choice.note:
            row.append("   ")
            row.append(choice.note, style=choice.tone)
        return row

    def _describe(self, index: int) -> None:
        if not (0 <= index < len(self._shown)):
            return
        self.query_one("#chooser-detail", Static).update(self._shown[index].description)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._describe(event.option_index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.id))

    # ---- the filter ------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        needle = event.value.strip()
        self._shown = [choice for choice in self._all if choice.matches(needle)]
        self._fill()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the filter takes the first match, which is what typing implies."""
        event.stop()
        self.action_choose()

    def on_key(self, event: events.Key) -> None:
        """Typing anywhere goes to the filter, so there is nothing to aim at.

        Only printable characters, and only when the filter is not already
        focused -- otherwise this would swallow the arrows the list needs.
        """
        if self.focused is self.query_one("#chooser-filter", Input):
            return
        if event.character and event.character.isprintable() and event.key != "space":
            field = self.query_one("#chooser-filter", Input)
            field.focus()
            field.value += event.character
            event.stop()
            event.prevent_default()

    # ---- finishing -------------------------------------------------------

    def action_choose(self) -> None:
        if not self._shown:
            return
        listing = self.query_one("#chooser-list", OptionList)
        index = listing.highlighted if listing.highlighted is not None else 0
        index = min(max(index, 0), len(self._shown) - 1)
        self.dismiss(self._shown[index].value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_back(self) -> None:
        """Return to the previous wizard step when one exists."""
        if self._allow_back:
            self.dismiss(NavigationAction.BACK)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "chooser-ok":
            self.action_choose()
        elif event.button.id == "chooser-back":
            self.action_back()
        else:
            self.action_cancel()


def can_run() -> bool:
    """Whether a modal makes sense here, rather than a typed prompt."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def choose(choices: list[Choice], *, title: str = "Choose one", filter_hint: str = "type to filter") -> str | None:
    """Open the chooser on its own and return the value picked.

    A standalone app rather than a pushed screen, because ``kbn init`` is a
    plain terminal wizard with no app running. It takes the terminal for as
    long as it is open and hands it back on the way out.
    """
    from textual.app import App  # noqa: PLC0415 - keeps the CLI's fast path light

    if not choices:
        return None

    class ChooserApp(Themed, App[str | None]):
        CSS = Chooser.DEFAULT_CSS
        TITLE = "pykantui"
        SUB_TITLE = "choose"

        def on_mount(self) -> None:
            self.apply_theme()
            self.push_screen(Chooser(choices, title=title, filter_hint=filter_hint), self.exit)

    return ChooserApp().run(inline=False)
