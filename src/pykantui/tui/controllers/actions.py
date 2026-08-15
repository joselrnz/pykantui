"""View-state action dispatch for the TUI."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from textual import work

from pykantui.core.actions import (
    Act,
    Action,
    ActionKind,
    ColumnCommand,
    HelpTopic,
    Menu,
    PaneAdjustment,
    ViewToggle,
)
from pykantui.core.filters import FilterState, SortKey
from pykantui.core.work_items import WorkItemColumn
from pykantui.i18n import translate as _
from pykantui.models import BoardLayout, MovementMode
from pykantui.pages.menu import PromptScreen
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.dropdowns import DateInput
from pykantui.tui.widgets.menu_bar import MenuBar
from pykantui.tui.widgets.work_items import WorkItemsView

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class ViewActionController:
    """Apply typed actions while leaving rendering in the app shell."""

    def action_toggle_movement(self) -> None:
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False):
            return
        app.movement_mode = (
            MovementMode.JUMP if app.movement_mode == MovementMode.ADJACENT else MovementMode.ADJACENT
        )
        app._update_subtitle()
        app.refresh_bindings()
        app.notify(_("Movement mode: {mode}").format(mode=_(str(app.movement_mode))), timeout=2)

    def action_toggle_confirm(self) -> None:
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False):
            return
        app.confirm_moves = not app.confirm_moves
        app._update_subtitle()
        app.notify(
            _("Moves will ask for confirmation") if app.confirm_moves else _("Moves apply immediately"),
            timeout=2,
        )

    @work
    async def _run_view_action(self, action: str | Action) -> None:
        """Carry out one action and render only when view state changed."""
        app = cast("KanbanApp", self)
        parsed = Action.parse(action)
        if parsed is None:
            return
        before = app.view.model_dump()
        if await app._apply(parsed) and app.view.model_dump() != before:
            await app.apply_view()

    async def _apply(self, action: Action) -> bool:
        """Return whether the board needs re-rendering afterwards."""
        app = cast("KanbanApp", self)
        view = app.view
        card_filter = view.card_filter

        match action.kind:
            case ActionKind.OPEN:
                menu = action.enum(Menu)
                if menu is not None:
                    bar = app.menu_bar
                    app.post_message(MenuBar.MenuRequested(menu, bar.region.x, bar.region.y + 1))
                return False
            case ActionKind.STATE:
                state = action.enum(FilterState)
                if state is None:
                    return False
                card_filter.toggle_state(state)
            case ActionKind.PICK_STATE:
                picked = action.enum(FilterState)
                card_filter.states = [picked] if picked is not None else []
            case ActionKind.PROJECT:
                card_filter.project = action.value
            case ActionKind.STATUS:
                card_filter.column_id = action.number
            case ActionKind.KEY:
                card_filter.key = action.value
            case ActionKind.FROM:
                card_filter.created_from = DateInput.parse(action.value)
            case ActionKind.UNTIL:
                card_filter.created_until = DateInput.parse(action.value)
            case ActionKind.SPRINT:
                app._set_sprint(action.flag)
            case ActionKind.RUN:
                await app._run_query(action.value)
                return False
            case ActionKind.PICK_PROVIDER:
                field, wanted = action.pair
                if wanted:
                    card_filter.provider[field] = wanted
                else:
                    card_filter.provider.pop(field, None)
            case ActionKind.SORT:
                sort = action.enum(SortKey)
                if sort is None:
                    return False
                view.sort = sort
            case ActionKind.SAVED:
                saved = app._saved_filters().get(action.value)
                if saved is None:
                    return False
                view.card_filter = saved.model_copy(deep=True)
                app.menu_bar.set_search(view.card_filter.text)
            case ActionKind.FOCUS:
                app.action_focus_filter(action.value)
                return False
            case ActionKind.ACT:
                act = action.enum(Act)
                return act is not None and await app._run_act(act)
            case ActionKind.VIEW:
                toggle = action.enum(ViewToggle)
                if toggle is None:
                    return False
                app._run_view_toggle(toggle)
            case ActionKind.LAYOUT:
                layout = action.enum(BoardLayout)
                if layout is None:
                    return False
                app.set_board_layout(layout)
                return False
            case ActionKind.PANE:
                adjustment = action.enum(PaneAdjustment)
                if adjustment is None or app.board_layout is not BoardLayout.SPLIT:
                    return False
                work_items = app.query_one(WorkItemsView)
                if adjustment is PaneAdjustment.NARROWER:
                    work_items.action_shrink_list()
                elif adjustment is PaneAdjustment.WIDER:
                    work_items.action_grow_list()
                else:
                    work_items.action_reset_split()
                return False
            case ActionKind.COL:
                command = action.enum(ColumnCommand)
                if command is not None:
                    await app._run_column_shortcut(command)
                return False
            case ActionKind.TABLE_COLUMN:
                column = action.enum(WorkItemColumn)
                if column is None:
                    return False
                return view.toggle_column(
                    column,
                    available=app.backend.available_task_fields(),
                )
            case ActionKind.HELP:
                topic = action.enum(HelpTopic)
                if topic is not None:
                    app._show_help(topic)
                return False

        return True

    def _set_sprint(self, active: bool) -> None:
        """Switch a Jira board between its sprint and its query."""
        app = cast("KanbanApp", self)
        if not app.backend.set_sprint_only(active):
            app.notify(_("Sprints are a Jira board setting"), severity="warning", timeout=3)
            return
        source = _("sprint") if active else "JQL"
        app.notify(_("Source: {source}. Press Search to re-run.").format(source=source), timeout=4)

    async def _run_query(self, query: str) -> None:
        """Run a read-only provider query and re-apply the local filters."""
        app = cast("KanbanApp", self)
        try:
            await asyncio.to_thread(app.backend.run_query, query)
        except Exception as error:  # provider boundaries normalise their own errors
            app.notify(str(error), title=_("Search"), severity="error", timeout=6)
            return
        await app.apply_view()

    async def _run_act(self, act: Act) -> bool:
        app = cast("KanbanApp", self)
        match act:
            case Act.REVERSE:
                app.view.reverse = not app.view.reverse
            case Act.CLEAR:
                app.view.card_filter.clear()
                app.view.sort = SortKey.MANUAL
                app.view.reverse = False
                app.menu_bar.set_search("")
            case Act.SAVE:
                await app._save_filter()
                return False
            case Act.NEW:
                app.action_new_task()
                return False
            case Act.REFRESH:
                await app.action_refresh_board()
                return False
            case Act.SYNC:
                app.action_sync_board()
                return False
            case Act.PROJECTS:
                app.action_projects()
                return False
        return True

    async def _save_filter(self) -> None:
        app = cast("KanbanApp", self)
        name = await app.push_screen_wait(PromptScreen("Name this filter"))
        config = app.backend.board_config()
        if name is None or config is None:
            return
        config.saved_filters[name] = app.view.card_filter.model_copy(deep=True)
        config.save()
        app.notify(_("Saved filter {name}").format(name=name), timeout=3)

    def _run_view_toggle(self, toggle: ViewToggle) -> None:
        app = cast("KanbanApp", self)
        match toggle:
            case ViewToggle.MOVEMENT:
                app.action_toggle_movement()
            case ViewToggle.CONFIRM:
                app.action_toggle_confirm()
            case ViewToggle.DETAIL:
                card = app.board.card_by_id(app.board.selected.task_id) if app.board.selected else None
                if card is not None:
                    app.post_message(TaskCard.DetailRequested(card))
            case ViewToggle.COLLAPSE:
                app.board.action_toggle_collapse()

    async def _run_column_shortcut(self, command: ColumnCommand) -> None:
        """Run a column command against the currently selected column."""
        app = cast("KanbanApp", self)
        if command is ColumnCommand.EXPAND_ALL:
            app.board.action_expand_all()
            return

        column = None
        if app.board.selected is not None:
            column = app.board.column_widget(app.board.selected.column_id)
        if column is None:
            columns = app.board.columns()
            column = columns[0] if columns else None
        if column is not None:
            await app._run_column_action(command, column, app.backend.board_config())

    def _show_help(self, topic: HelpTopic) -> None:
        app = cast("KanbanApp", self)
        if topic is HelpTopic.WHERE:
            config = app.backend.board_config()
            where = str(config.path) if config and config.path else _("in memory (demo board)")
            app.notify(
                _("Columns and filters: {path}").format(path=where),
                title=_("Where things are stored"),
                timeout=10,
            )
            return
        app.notify(
            _(
                "hjkl move focus · HL move card · JK reorder · v detail · , menu\n"
                "z collapse · Z expand all · n new · e edit · d delete · r reload · F5 sync"
            ),
            title=_("Keys"),
            timeout=15,
        )
