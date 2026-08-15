"""Confirmation modal shown before a card changes column.

Moving a card is the one action with a consequence outside the board — against
Jira it executes a workflow transition — so it asks first. Reordering inside a
column does not, because nothing leaves the column.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from pykantui.i18n import translate as _


class ConfirmMoveScreen(ModalScreen[bool]):
    """Returns ``True`` to move, ``False`` to leave the card where it is."""

    BINDINGS = [
        Binding("escape,n", "cancel", "Cancel"),
        Binding("enter,y", "approve", "Move"),
    ]

    def __init__(self, title: str, origin: str, destination: str, warning: str = "") -> None:
        super().__init__()
        self.card_title = title.splitlines()[0]
        self.origin = origin
        self.destination = destination
        self.warning = warning

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(_("Move this card?"), id="confirm-heading")
            yield Label(self.card_title, id="confirm-card")
            yield Label(f"{self.origin}  →  {self.destination}", id="confirm-route")
            if self.warning:
                yield Label(self.warning, id="confirm-warning")
            with Horizontal(id="confirm-buttons"):
                yield Button(_("Move"), variant="primary", id="confirm-move")
                yield Button(_("Cancel"), id="confirm-cancel")

    def on_mount(self) -> None:
        # Focus Move so enter is the fast path, but the user still has to act.
        self.query_one("#confirm-move", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-move")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
