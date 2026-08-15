"""Provider-aware menu-bar orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual import work
from textual.geometry import Offset

from pykantui.core.actions import Menu
from pykantui.core.filters import CardFilter
from pykantui.models import MenuLevel
from pykantui.pages.menu import ContextMenuScreen, MenuItem
from pykantui.tui.menu_items import build_menu_items
from pykantui.tui.widgets.menu_bar import MenuBar

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp

FILTER_PREFIX = "filter-"


class MenuController:
    """Translate menu events into the application's shared action vocabulary."""

    def action_cycle_menu_bar(self) -> None:
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False):
            return
        app.menu_bar.cycle()

    def action_focus_filter(self, widget_id: str) -> None:
        """Jump to a dropdown by its shortcut, opening the panel if needed."""
        bar = cast("KanbanApp", self).menu_bar
        if bar.level is not MenuLevel.EXPANDED:
            bar.level = MenuLevel.EXPANDED
        found = bar.query(f"#{widget_id}")
        if found:
            found.first().focus()

    def action_focus_search(self) -> None:
        """Jump to the search box, expanding the bar if it is hidden."""
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False):
            return
        bar = app.menu_bar
        if bar.level is MenuLevel.COLLAPSED:
            bar.level = MenuLevel.EXPANDED
        bar.query_one("#bar-search").focus()

    def on_menu_bar_level_changed(self, event: MenuBar.LevelChanged) -> None:
        """Remember how much of the bar the user wants, per board."""
        app = cast("KanbanApp", self)
        config = app.backend.board_config()
        if config is not None and config.menu_level != event.level:
            config.menu_level = event.level
            config.save()

    async def on_menu_bar_search_changed(self, event: MenuBar.SearchChanged) -> None:
        app = cast("KanbanApp", self)
        app.view.card_filter.text = event.text
        await app.apply_view()

    def on_menu_bar_chip_pressed(self, event: MenuBar.ChipPressed) -> None:
        cast("KanbanApp", self)._run_view_action(event.action)

    @work
    async def on_menu_bar_menu_requested(self, event: MenuBar.MenuRequested) -> None:
        app = cast("KanbanApp", self)
        items = app._menu_items(event.menu)
        if not items:
            return
        chosen = await app.push_screen_wait(
            ContextMenuScreen(
                event.menu.label,
                items,
                anchor_at=Offset(event.anchor_x, event.anchor_y),
            )
        )
        if chosen is not None:
            app._run_view_action(chosen)

    def _menu_items(self, menu: Menu) -> list[MenuItem]:
        """Build provider-aware rows through the pure menu model."""
        app = cast("KanbanApp", self)
        return build_menu_items(
            menu,
            view=app.view,
            board_layout=app.board_layout,
            movement_mode=app.movement_mode,
            confirm_moves=app.confirm_moves,
            supports_sync=app.backend.supports_sync,
            provider_fields=app.backend.provider_filter_fields(),
            saved_filters=app._saved_filters(),
            filter_prefix=FILTER_PREFIX,
            available_columns=app.backend.available_task_fields(),
        )

    def _saved_filters(self) -> dict[str, CardFilter]:
        app = cast("KanbanApp", self)
        config = app.backend.board_config()
        return config.saved_filters if config else {}
