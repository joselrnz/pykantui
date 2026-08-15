"""Confirmation shown before leaving local provider work behind."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from pykantui.i18n import translate as _


class ConfirmProjectSwitchScreen(ModalScreen[bool]):
    """Require an explicit decision when the current workspace needs attention."""

    BINDINGS = [
        Binding("escape,n", "cancel", "Cancel"),
        Binding("y", "approve", "Switch anyway"),
    ]

    DEFAULT_CSS = """
    ConfirmProjectSwitchScreen { align: center middle; }
    ConfirmProjectSwitchScreen #project-switch-dialog {
        width: 72;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    ConfirmProjectSwitchScreen #project-switch-heading {
        color: $warning;
        text-style: bold;
    }
    ConfirmProjectSwitchScreen #project-switch-warning {
        width: 100%;
        height: auto;
        margin: 1 0;
        color: $text;
    }
    ConfirmProjectSwitchScreen #project-switch-target {
        width: 100%;
        height: auto;
        color: $text-muted;
    }
    ConfirmProjectSwitchScreen #project-switch-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    ConfirmProjectSwitchScreen Button {
        min-width: 18;
        margin-left: 1;
        border: round $primary;
        background: transparent;
    }
    ConfirmProjectSwitchScreen #project-switch-confirm {
        color: $warning;
        border: round $warning;
    }
    """

    def __init__(self, warning: str, target: str) -> None:
        super().__init__()
        self.warning = warning
        self.target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="project-switch-dialog"):
            yield Label(_("Leave this workspace?"), id="project-switch-heading")
            yield Label(self.warning, id="project-switch-warning")
            yield Label(self.target, id="project-switch-target")
            with Horizontal(id="project-switch-buttons"):
                yield Button(_("Cancel"), id="project-switch-cancel")
                yield Button(_("Switch anyway"), id="project-switch-confirm")

    def on_mount(self) -> None:
        self.query_one("#project-switch-cancel", Button).focus()

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "project-switch-confirm")


__all__ = ["ConfirmProjectSwitchScreen"]
