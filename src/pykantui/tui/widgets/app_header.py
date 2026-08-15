"""One global application header for menu, provider sync, and exit."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click
from textual.widgets import Label

from pykantui.i18n import translate as _

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class AppHeader(Horizontal):
    """Keep global actions in one predictable row.

    The old Textual ``Header`` circle and pykantui's second ``⌘ Menu`` entry
    represented the same destination.  This header names that destination and
    leaves the filter bar for board controls only.
    """

    app: KanbanApp

    def __init__(self) -> None:
        super().__init__(id="app-header")

    def compose(self) -> ComposeResult:
        yield Label(_("⌘ Menu"), id="app-header-menu")
        yield Label("", id="app-header-title")
        yield Label(f"⎈ {self.app.backend.display_kind()}", id="app-header-provider")
        if self.app.backend.supports_sync:
            yield Label(_("⎇ Sync"), id="app-header-sync")
        yield Label("×", id="app-header-exit")

    def on_mount(self) -> None:
        self.watch(self.app, "title", self._refresh_title)
        self.watch(self.app, "sub_title", self._refresh_title)
        self._refresh_title()

    def _refresh_title(self) -> None:
        title = self.app.title
        subtitle = self.app.sub_title
        self.query_one("#app-header-title", Label).update(f"{title} — {subtitle}" if subtitle else title)

    @on(Click, "#app-header-menu")
    def open_menu(self, event: Click) -> None:
        event.stop()
        self.app.action_command_palette()

    @on(Click, "#app-header-sync")
    def sync(self, event: Click) -> None:
        event.stop()
        self.app.action_sync_board()

    @on(Click, "#app-header-exit")
    async def exit_app(self, event: Click) -> None:
        event.stop()
        await self.app.action_quit()
