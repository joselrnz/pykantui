"""Small modals: a context menu, and a one-line prompt."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Click, Resize
from textual.geometry import Offset
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

#: Must match ``#menu-dialog`` in app.tcss.
MENU_WIDTH = 34

#: Border, top padding, heading and its blank line — everything but the items.
MENU_CHROME_ROWS = 5


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str


class ContextMenuScreen(ModalScreen[str | None]):
    """Pick an action. Returns the chosen item's key, or ``None``.

    The caller decides which items to offer, so an action that cannot work —
    editing cards on a read-only backend, say — is simply not on the menu
    rather than failing after you pick it.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, items: list[MenuItem], anchor_at: Offset | None = None) -> None:
        super().__init__()
        self.menu_title = title
        self.items = items

        # anchor_at, not anchor: Textual's Screen already has an anchor()
        # method, and shadowing it breaks the screen.
        #: Screen cell to drop the menu from — the header it belongs to. Without
        #: one it opens in the middle, which is right for a confirmation but
        #: wrong for a dropdown.
        self.anchor_at = anchor_at

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-dialog"):
            yield Label(self.menu_title, id="menu-heading")
            yield OptionList(*[Option(item.label, id=item.key) for item in self.items], id="menu-options")

    def on_mount(self) -> None:
        self.query_one("#menu-options", OptionList).focus()
        # Place immediately from the predicted size so the menu never renders in
        # the wrong spot, then correct once layout has given it a real one.
        self._place()
        self.call_after_refresh(self._place)

    def _predicted_size(self) -> tuple[int, int]:
        """Size before layout has run.

        Waiting for a real size means one frame in the wrong place, and a test
        that has to guess how many refreshes to wait. The shape is known: a
        border, a heading and a blank line, then one row per item.
        """
        return (
            min(MENU_WIDTH, max(1, self.size.width)),
            min(len(self.items) + MENU_CHROME_ROWS, max(1, self.size.height)),
        )

    def _place(self) -> None:
        """Put the menu under its anchor, kept inside the screen."""
        dialog = self.query_one("#menu-dialog", Vertical)
        width, height = dialog.outer_size.width, dialog.outer_size.height
        if width <= 0 or height <= 0:
            width, height = self._predicted_size()
        screen_width, screen_height = self.size.width, self.size.height

        if self.anchor_at is None:
            x = max(0, (screen_width - width) // 2)
            y = max(0, (screen_height - height) // 2)
        else:
            x = min(self.anchor_at.x, max(0, screen_width - width))
            y = self.anchor_at.y
            # Flip above the anchor rather than running off the bottom.
            if y + height > screen_height:
                y = max(0, min(self.anchor_at.y, screen_height - height))

        dialog.styles.offset = (x, y)

    def on_resize(self, event: Resize) -> None:
        """Reflow an open menu when its terminal becomes smaller or larger."""
        del event
        self.call_after_refresh(self._place)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_click(self, event: Click) -> None:
        """Dismiss a context menu when the pointer lands outside its panel."""
        if not self.query_one("#menu-dialog", Vertical).region.contains_point(event.screen_offset):
            event.stop()
            event.prevent_default()
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PromptScreen(ModalScreen[str | None]):
    """Ask for one line of text. Returns it, or ``None`` if cancelled."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self.prompt_title = title
        self.value = value
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-dialog"):
            yield Label(self.prompt_title, id="prompt-heading")
            yield Input(value=self.value, placeholder=self.placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
