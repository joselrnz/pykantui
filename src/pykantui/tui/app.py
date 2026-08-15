"""The Textual application."""

from __future__ import annotations

from collections.abc import Iterable
from functools import partial
from inspect import isawaitable
from typing import Literal

from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.events import DescendantFocus
from textual.screen import Screen
from textual.theme import Theme

from pykantui.config import DEFAULT_THEME
from pykantui.core.actions import (
    Act,
    Action,
    ActionKind,
    Menu,
)
from pykantui.core.filters import (
    BoardView,
    finished_ids,
    project_of,
)
from pykantui.i18n import ntranslate as ngettext
from pykantui.i18n import translate as _
from pykantui.models import BoardLayout, Column, Edges, MenuLevel, MovementMode, Task
from pykantui.pages.grouped_palette import GroupedCommandPalette, PaletteCommand, PaletteNode
from pykantui.pages.sync import SyncProgressScreen
from pykantui.sync.base import Backend
from pykantui.tui.controllers import (
    CardController,
    ColumnController,
    MenuController,
    ProjectController,
    SyncController,
    ViewActionController,
)
from pykantui.tui.palette import build_palette_tree
from pykantui.tui.terminal import TerminalResizeMixin
from pykantui.tui.themes import CUSTOM_THEMES
from pykantui.tui.widgets.app_header import AppHeader
from pykantui.tui.widgets.board import KanbanBoard
from pykantui.tui.widgets.compact_footer import CompactFooter
from pykantui.tui.widgets.menu_bar import MenuBar
from pykantui.tui.widgets.work_items import WorkItemsView


class KanbanApp(
    CardController,
    MenuController,
    ProjectController,
    ViewActionController,
    SyncController,
    ColumnController,
    TerminalResizeMixin,
    App[None],
):
    CSS_PATH = "app.tcss"
    TITLE = "pykantui"

    #: Textual otherwise focuses the first focusable widget it finds, which is
    #: the menu bar's search box — before the board has mounted. The board then
    #: declines to steal focus, the bar hides the box at its collapsed level,
    #: and nothing is focused at all: every key press goes nowhere. The board
    #: grants the initial focus itself.
    AUTO_FOCUS = None

    _SYNC_LOCKED_ACTIONS = frozenset(
        {
            "new_task",
            "refresh_board",
            "toggle_movement",
            "toggle_confirm",
            "toggle_team",
            "focus_search",
            "cycle_menu_bar",
            "command_palette",
        }
    )

    BINDINGS = [
        Binding("n", "new_task", "New", priority=True, show=False),
        Binding("r", "refresh_board", "Reload", priority=True, show=False),
        Binding("f5", "sync_board", "⎇ Sync", priority=True, key_display="F5", show=False),
        Binding("m", "toggle_movement", "Move mode", priority=True, show=False),
        Binding("c", "toggle_confirm", "Confirm", priority=True, show=False),
        # NOT "e": the card binds e/enter to Edit, and a priority binding
        # here silently won -- so adding the team view took editing away.
        Binding("T", "toggle_team", "Team", priority=True, key_display="T", show=False),
        Binding("slash", "focus_search", "Search", priority=True, show=False),
        Binding("f2", "cycle_menu_bar", "Filter bar", priority=True, show=False),
        # Filter shortcuts. Deliberately *not* priority: a priority binding
        # fires even while an Input has focus, so typing "p" into the search
        # box would jump focus instead of typing a letter.
        #
        # Four letters differ from jiratui's because this board has vim
        # navigation and theirs does not: k/j are move-up/down, e is edit and
        # v is view, so Key, JQL, State and Sprint take w/q/y/x instead.
        Binding("p", "focus_filter('filter-project')", "Project", show=False),
        Binding("t", "focus_filter('filter-provider-issue_type')", "Type", show=False),
        Binding("s", "focus_filter('filter-status')", "Status", show=False),
        Binding("a", "focus_filter('filter-provider-assignee')", "Assignee", show=False),
        Binding("y", "focus_filter('filter-state')", "State", show=False),
        Binding("w", "focus_filter('filter-key')", "Key", show=False),
        Binding("f", "focus_filter('filter-created-from')", "From", show=False),
        Binding("u", "focus_filter('filter-created-until')", "Until", show=False),
        Binding("o", "focus_filter('filter-sort')", "Sort", show=False),
        Binding("g", "focus_filter('filter-saved')", "Saved", show=False),
        Binding("x", "focus_filter('filter-sprint')", "Sprint", show=False),
        Binding("q", "focus_filter('filter-query')", "Query", show=False),
        Binding("ctrl+q", "quit", "⎋ Quit", priority=True, show=False),
    ]

    def __init__(
        self,
        backend: Backend,
        movement_mode: MovementMode = MovementMode.ADJACENT,
        confirm_moves: bool = True,
    ) -> None:
        super().__init__()
        self.backend = backend
        self.title = f"pykantui · {backend.display_kind()}"
        self.sub_title = backend.get_active_board().name
        self.movement_mode = movement_mode

        #: Ask before a card changes column. On by default: a move is the one
        #: action with an effect outside the board.
        self.confirm_moves = confirm_moves

        #: The filter and sort currently applied. Presentation only — nothing
        #: here is ever written back to the store.
        self.view = BoardView()
        self.board_layout = BoardLayout.KANBAN
        self._sync_in_flight = False
        self._unknown_theme: str | None = None

        # Theme selection changes CSS across the entire widget tree.  Doing it
        # before compose avoids mounting a large board under Textual's default
        # theme only to restyle every node again in ``on_mount``.
        self._register_themes()
        self._apply_theme()

    def compose(self) -> ComposeResult:
        config = self.backend.board_config()
        yield AppHeader()
        yield MenuBar(level=config.menu_level if config else MenuLevel.COLLAPSED)
        yield KanbanBoard()
        yield WorkItemsView()
        yield CompactFooter()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide board-changing shortcuts while sync owns the modal workflow."""
        if getattr(self, "_sync_in_flight", False) and action in self._SYNC_LOCKED_ACTIONS:
            return False
        return super().check_action(action, parameters)

    def get_system_commands(self, screen: Screen[object]) -> Iterable[SystemCommand]:
        """Merge pykantui's provider-aware actions into Textual's palette."""
        yield from super().get_system_commands(screen)

        for item in self._menu_items(Menu.MAIN):
            action = Action.parse(item.key)
            if action is None or action.kind is ActionKind.OPEN:
                continue
            title = self._palette_main_title(action, item.label)
            yield SystemCommand(
                title,
                _("Run this pykantui board command"),
                partial(self._run_view_action, item.key),
            )

        for menu in (Menu.FILTER, Menu.SORT, Menu.COLUMNS, Menu.VIEW, Menu.HELP):
            for item in self._menu_items(menu):
                label = self._plain_menu_label(item.label)
                yield SystemCommand(
                    f"{menu.value.title()} · {label}",
                    _("Run the {menu} command in pykantui").format(menu=_(menu.value)),
                    partial(self._run_view_action, item.key),
                )

    def _palette_main_title(self, action: Action, label: str) -> str:
        if action.kind is ActionKind.ACT and action.enum(Act) is Act.SYNC:
            return _("Sync with {provider}…").format(provider=self.backend.display_kind())
        return self._plain_menu_label(label)

    @staticmethod
    def _plain_menu_label(label: str) -> str:
        if label.startswith(("✓ ", "  ")):
            return label[2:]
        return label.removeprefix("⌂ ")

    def action_command_palette(self) -> None:
        """Open pykantui's wide grouped palette from the header or Ctrl+P."""
        if self._sync_in_flight or isinstance(self.screen, SyncProgressScreen):
            return
        if isinstance(self.screen, GroupedCommandPalette):
            return
        self.push_screen(
            GroupedCommandPalette(self._palette_tree()),
            self._run_palette_command,
        )

    async def _run_palette_command(self, command: PaletteCommand | None) -> None:
        """Run a selected command after the palette has left the screen."""
        if command is None:
            return
        result = command.callback()
        if isawaitable(result):
            await result

    def _palette_tree(self) -> tuple[PaletteNode, ...]:
        """Build the palette tree from the same actions as the toolbar menus."""
        return build_palette_tree(
            display_kind=self.backend.display_kind(),
            menu_items=self._menu_items,
            main_title=self._palette_main_title,
            run_action=self._run_view_action,
            system_commands=super().get_system_commands(self.screen),
        )

    # ---- what the board is showing --------------------------------------

    def view_summary(self) -> str:
        """The count for the bar, and what is filtering, if anything."""
        total = len(self.backend.get_tasks())
        if not self.view.active:
            noun = ngettext("card", "cards", total)
            return f"{total} {noun}"
        shown = len(self.visible_tasks())
        detail = self.view.summary()
        if detail:
            return _("{shown} of {total} · {detail}").format(shown=shown, total=total, detail=detail)
        return _("{shown} of {total}").format(shown=shown, total=total)

    def saved_filter_names(self) -> list[str]:
        return sorted(self._saved_filters())

    def project_names(self) -> list[str]:
        """Projects present on the board, from the issue key prefixes."""
        return sorted({project_of(task) for task in self.backend.get_tasks() if project_of(task)})

    def column_choices(self) -> list[tuple[str, int]]:
        return [(column.name, column.column_id) for column in self.visible_columns]

    def provider_values(self, field: str) -> list[str]:
        """Every value a provider field actually takes on this board.

        Offering only what is present beats a free-text prompt: you cannot
        filter on an assignee who has nothing here.
        """
        seen: set[str] = set()
        for task in self.backend.get_tasks():
            value = task.metadata.get(field)
            if isinstance(value, list):
                seen.update(str(item) for item in value if item)
            elif value:
                seen.add(str(value))
        return sorted(seen)

    def visible_tasks(self) -> list[Task]:
        tasks = self.backend.get_tasks()
        return self.view.apply(tasks, finished_ids=finished_ids(tasks))

    def refresh_view(self) -> None:
        bar = self.menu_bar
        bar.refresh_status()
        bar.refresh_chips()
        self.refresh_bindings()

    @property
    def menu_bar(self) -> MenuBar:
        return self.query_one(MenuBar)

    async def apply_view(self) -> None:
        """Re-render the board for the current filter and sort."""
        if self.board_layout is BoardLayout.KANBAN:
            await self.board.refresh_board()
        else:
            self.query_one(WorkItemsView).refresh_tasks()
        self.refresh_view()

    def on_mount(self) -> None:
        # Dragging a card must move it, not select the words it passes over.
        # Per-screen rather than per-app, so modals stay selectable — copying an
        # issue key out of the detail view is worth keeping.
        # Textual declares it as a class variable, but Screen.allow_select
        # reads it off the instance, so shadowing it here affects this
        # screen alone — which is exactly the scope we want.
        self.screen.ALLOW_SELECT = False  # type: ignore[misc]
        self.theme_changed_signal.subscribe(self, self._remember_theme)
        self._apply_edges()
        self.set_board_layout(BoardLayout.KANBAN)
        self._update_subtitle()
        self._start_terminal_resize_monitor()
        if self._unknown_theme is not None:
            self.notify(
                _("Unknown theme {theme!r}; using {fallback}").format(
                    theme=self._unknown_theme,
                    fallback=DEFAULT_THEME,
                ),
                severity="warning",
                timeout=6,
            )
        for warning in self.backend.warnings():
            self.notify(warning, title=_("Check your configuration"), severity="warning", timeout=10)

    def _register_themes(self) -> None:
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)

    def _apply_theme(self) -> None:
        """Set the theme, falling back if the name is not one Textual knows.

        A typo in config.json should not stop the board opening.
        """
        config = self.backend.board_config()
        wanted = config.theme if config else DEFAULT_THEME
        if wanted in self.available_themes:
            self.theme = wanted
            self._unknown_theme = None
            return
        self.theme = DEFAULT_THEME
        self._unknown_theme = wanted

    def _remember_theme(self, theme: Theme) -> None:
        """Persist choices made through Textual's Ctrl+P theme palette."""
        config = self.backend.board_config()
        if config is not None and config.theme != theme.name:
            config.theme = theme.name
            config.save()
        self._refresh_footer()

    def _apply_edges(self) -> None:
        """Round or square corners, as one class on the screen.

        A class rather than per-widget styling, so every border in the app
        switches together and nothing can be left behind on the other style.
        """
        config = self.backend.board_config()
        edges = config.edges if config else Edges.ROUND
        self.screen.set_class(edges is Edges.SQUARE, "edges-square")

    def _update_subtitle(self) -> None:
        confirm = _("confirm on") if self.confirm_moves else _("confirm off")
        movement = _(str(self.movement_mode))
        self.sub_title = (
            f"{self.backend.get_active_board().name}  ·  {self.board_layout.label}  ·  "
            f"{movement} {_('mode')}  ·  {confirm}"
        )

    # ---- shared state the widgets read ---------------------------------

    @property
    def visible_columns(self) -> list[Column]:
        return self.backend.get_visible_columns()

    @property
    def board(self) -> KanbanBoard:
        return self.query_one(KanbanBoard)

    def set_board_layout(self, layout: BoardLayout) -> None:
        """Switch the workspace; Home and Close both resolve to Kanban."""
        board = self.query_one(KanbanBoard)
        work_items = self.query_one(WorkItemsView)
        if work_items.editor_active and layout is not BoardLayout.SPLIT:
            self.notify(
                f"{_('Save locally')} · {_('Cancel')}",
                title=_("Edit"),
                severity="warning",
                timeout=3,
            )
            return
        previous_layout = self.board_layout
        self.board_layout = layout
        board.display = layout is BoardLayout.KANBAN
        work_items.display = layout is not BoardLayout.KANBAN
        work_items.set_layout(layout)
        if work_items.display:
            work_items.refresh_tasks()
            work_items.query_one("#work-items-table").focus()
        else:
            if previous_layout is not BoardLayout.KANBAN:
                self._refresh_kanban_after_layout_switch()
            focused_card = None
            if board.selected is not None:
                focused_card = board.card_by_id(board.selected.task_id)
            if focused_card is not None:
                focused_card.focus()
            else:
                # The board and app mount concurrently. On slower event loops
                # (notably Linux CI), the initial layout pass can run before a
                # TaskCard has published its Focused message. Give keyboard
                # actions a deterministic receiver now; if cards are still
                # mounting, the focused board makes rebuild() focus the first.
                board.focus_first_card()
        self.menu_bar.refresh_layout()
        self._update_subtitle()
        self._refresh_footer()

    @work(group="layout-render", exclusive=True)
    async def _refresh_kanban_after_layout_switch(self) -> None:
        """Bring a previously hidden Kanban tree up to date once it is visible."""
        if self.board_layout is BoardLayout.KANBAN:
            await self.board.refresh_board()

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        """Keep the bottom hints aligned with the widget the user is using."""
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        footers = self.query(CompactFooter)
        if footers:
            footers.first().refresh_context()

    def action_home(self) -> None:
        """Home is the canonical Kanban board, not a fourth view."""
        self.set_board_layout(BoardLayout.KANBAN)

    async def action_quit(self) -> None:
        """Never abandon recoverable work or an in-flight provider write."""
        if self._sync_in_flight:
            self.notify(
                _("Sync is still running · wait for its final status"),
                title=_("Sync"),
                severity="warning",
                timeout=3,
            )
            return
        views = self.query(WorkItemsView)
        if views and views.first().editing:
            self.notify(
                f"{_('Save locally')} · {_('Cancel')}",
                title=_("Edit"),
                severity="warning",
                timeout=3,
            )
            return
        self.exit()

    def neighbour_column(self, column_id: int, direction: Literal["left", "right"]) -> int:
        """The column id one step from ``column_id``, wrapping at both ends."""
        column_ids = [column.column_id for column in self.visible_columns]
        if column_id not in column_ids:
            return column_id
        step = -1 if direction == "left" else 1
        return column_ids[(column_ids.index(column_id) + step) % len(column_ids)]
