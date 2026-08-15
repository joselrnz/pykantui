"""The top bar: a menu button, a search box, and the filter chips.

Three levels of the same state. Collapsing never changes what is filtered, only
how much of it you can see — and the count stays visible at every level, so a
filter you forgot about is never invisible.

Nothing here decides what a click *does*. Every control posts an
:class:`~pykantui.core.actions.Action` and the app carries it out, which is why
a chip and the matching row in a menu always behave the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, Input, Label, Select

from pykantui.core.actions import Act, Action, ActionKind, Menu
from pykantui.core.filters import BoardView, FilterState, SortKey
from pykantui.i18n import translate as _
from pykantui.models import BoardLayout, MenuLevel
from pykantui.tracker.filter_fields import FilterFieldName, FilterFieldSpec
from pykantui.tui.widgets.dropdowns import (
    LabelledSelect,
    active_sprint_checkbox,
    created_from_input,
    created_until_input,
    key_input,
    project_select,
    provider_select,
    query_input,
    saved_select,
    search_button,
    search_input,
    sort_select,
    state_select,
    status_select,
)

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp

#: Widget ids the bar owns, and the dropdowns whose value it mirrors.
STATE_SELECT = "filter-state"
SORT_SELECT = "filter-sort"
PROJECT_SELECT = "filter-project"
STATUS_SELECT = "filter-status"
SAVED_SELECT = "filter-saved"
PROVIDER_SELECT_PREFIX = "filter-provider-"

# Header and footer each occupy one row.  The expanded provider controls may
# scroll, but the board itself must never be squeezed into an unusable strip.
MIN_WORKSPACE_HEIGHT = 12
APP_CHROME_HEIGHT = 2
MIN_EXPANDED_MENU_HEIGHT = 3


class MenuBar(Vertical):
    app: KanbanApp

    level: reactive[MenuLevel] = reactive(MenuLevel.COLLAPSED, init=False)

    class MenuRequested(Message):
        """A menu label was clicked; the app owns what is in each menu."""

        def __init__(self, menu: Menu, anchor_x: int, anchor_y: int) -> None:
            self.menu = menu
            self.anchor_x = anchor_x
            self.anchor_y = anchor_y
            super().__init__()

    class SearchChanged(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class ChipPressed(Message):
        """A control in the bar was used.

        Carries the action itself rather than a string, so the app never has to
        guess which chip a widget id belonged to.
        """

        def __init__(self, action: Action) -> None:
            self.action = action
            super().__init__()

    class LevelChanged(Message):
        def __init__(self, level: MenuLevel) -> None:
            self.level = level
            super().__init__()

    def __init__(self, level: MenuLevel = MenuLevel.COLLAPSED) -> None:
        super().__init__(id="menu-bar")
        self.set_reactive(MenuBar.level, level)

    def compose(self) -> ComposeResult:
        with Horizontal(id="bar-row"):
            yield Label(_("⌂ Home"), id="bar-home", classes="bar-command")
            yield search_input()
            for menu in Menu.in_bar():
                yield Label(menu.label, id=f"bar-menu-{menu.value}", classes="bar-label")
            yield Label("", id="bar-status")
            yield Label("▾", id="bar-caret")
        yield VerticalScroll(id="bar-panel")

    def on_mount(self) -> None:
        self._build_panel()
        self._apply_level()
        self.refresh_status()

    # ---- level ---------------------------------------------------------

    def cycle(self) -> None:
        self.level = self.level.next

    def watch_level(self) -> None:
        if self._ready():
            self._apply_level()
            self.post_message(self.LevelChanged(self.level))

    def _apply_level(self) -> None:
        expanded = self.level is MenuLevel.EXPANDED
        self.query_one("#bar-search", Input).display = self.level >= MenuLevel.TOOLBAR
        for menu in Menu.in_bar():
            self.query_one(f"#bar-menu-{menu.value}", Label).display = self.level >= MenuLevel.TOOLBAR
        toolbar = self.level >= MenuLevel.TOOLBAR
        self.query_one("#bar-home", Label).display = toolbar
        self.query_one("#bar-panel", VerticalScroll).display = expanded
        self.query_one("#bar-caret", Label).update("▴" if expanded else "▾")
        self.styles.height = "auto" if expanded else 1
        self._apply_vertical_budget()
        self.refresh_layout()
        self.refresh_status()

    def on_resize(self, _event: events.Resize) -> None:
        """Rebudget an expanded filter panel without changing its state."""
        self._apply_vertical_budget()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Reveal keyboard-selected controls inside a capped filter panel."""
        if self.level is not MenuLevel.EXPANDED:
            return
        if any(ancestor.id == "bar-panel" for ancestor in event.widget.ancestors):
            event.widget.scroll_visible(animate=False, force=True, immediate=True)

    def _apply_vertical_budget(self) -> None:
        """Reserve a usable board and let oversized provider controls scroll."""
        panel = self.query("#bar-panel")
        if not panel:
            return
        if self.level is not MenuLevel.EXPANDED:
            self.styles.max_height = None
            panel.first(VerticalScroll).styles.max_height = None
            return

        maximum = max(
            MIN_EXPANDED_MENU_HEIGHT,
            self.app.size.height - APP_CHROME_HEIGHT - MIN_WORKSPACE_HEIGHT,
        )
        self.styles.max_height = maximum
        panel.first(VerticalScroll).styles.max_height = max(1, maximum - 1)

    def refresh_layout(self) -> None:
        """Make the View menu's glyph identify the active layout."""
        if not self._ready():
            return
        self.query_one("#bar-menu-view", Label).update(f"{self.app.board_layout.glyph} {_('View')}")

    # ---- panel ---------------------------------------------------------

    def _build_panel(self) -> None:
        """Build shared controls plus only the current provider's boxes."""
        panel = self.query_one("#bar-panel", VerticalScroll)
        backend = self.app.backend
        fields = {field.name: field for field in backend.provider_filter_fields()}

        primary = self._provider_controls(
            fields,
            (
                FilterFieldName.SCOPE,
                FilterFieldName.STATUS,
                FilterFieldName.ASSIGNEE,
                FilterFieldName.ISSUE_TYPE,
            ),
        )
        if FilterFieldName.STATUS not in fields:
            primary.append(status_select(self.app.column_choices()))

        secondary = self._provider_controls(
            fields,
            (FilterFieldName.PRIORITY, FilterFieldName.LABELS),
        )

        key = fields.get(FilterFieldName.KEY)
        key_control = (
            key_input(title=key.label, placeholder=key.placeholder, provider=True) if key is not None else key_input()
        )
        bottom_controls = [key_control, created_from_input(), created_until_input(), sort_select()]
        sprint = fields.get(FilterFieldName.SPRINT)
        if sprint is not None:
            bottom_controls.append(
                active_sprint_checkbox(
                    sprint.label,
                    backend.sprint_only(),
                    enabled=backend.can_run_query(),
                )
            )
        query = fields.get(FilterFieldName.QUERY)
        if query is not None:
            bottom_controls.extend(
                [
                    query_input(
                        query.label,
                        query.query_language,
                        backend.query_text(),
                        enabled=backend.can_run_query(),
                    ),
                    search_button(enabled=backend.can_run_query()),
                ]
            )
        bottom = Grid(
            *bottom_controls,
            classes=f"filter-grid filter-grid-{len(bottom_controls)}",
        )
        extras = Horizontal(
            state_select(),
            saved_select(self.app.saved_filter_names()),
            self._chip(Act.REVERSE, _("⇵ Reverse")),
            self._chip(Act.SAVE, _("+ Save")),
            self._chip(Act.CLEAR, _("Clear")),
            classes="filter-row",
        )
        rows = [Horizontal(*primary, classes="filter-row")]
        if secondary:
            rows.append(Horizontal(*secondary, classes="filter-row"))
        panel.mount_all([*rows, bottom, extras])
        self.refresh_chips()

    def _provider_controls(
        self,
        fields: dict[FilterFieldName, FilterFieldSpec],
        order: tuple[FilterFieldName, ...],
    ) -> list[LabelledSelect]:
        controls: list[LabelledSelect] = []
        for name in order:
            field = fields.get(name)
            if field is None:
                continue
            if name is FilterFieldName.SCOPE:
                controls.append(project_select(self.app.project_names(), title=field.label))
            elif name is FilterFieldName.STATUS:
                controls.append(status_select(self.app.column_choices(), title=field.label, provider=True))
            else:
                controls.append(provider_select(name.value, field.label, self.app.provider_values(name.value)))
        return controls

    @staticmethod
    def _chip(act: Act, label: str) -> Label:
        """A chip, with its action encoded into its widget id.

        The id is the only thing a Textual click event carries back, so it has
        to be enough to reconstruct the action — see ``Action.from_chip_id``.
        """
        return Label(label, id=Action.of(ActionKind.ACT, act).chip_id, classes="chip")

    # ---- rendering -----------------------------------------------------

    def _ready(self) -> bool:
        """Whether compose() has run.

        Not ``is_mounted``: that is still False inside on_mount, so guarding on
        it silently skips the very first render.
        """
        return bool(self.query("#bar-status"))

    def refresh_status(self) -> None:
        if not self._ready():
            return
        self.query_one("#bar-status", Label).update(self.app.view_summary())

    def refresh_chips(self) -> None:
        """Light the toggles, and point each dropdown at the current view."""
        if not self._ready():
            return
        view: BoardView = self.app.view
        for chip in self.query(".chip").results(Label):
            action = Action.from_chip_id(str(chip.id or ""))
            chip.set_class(action is not None and self._chip_active(view, action), "-on")

        for select in self.query(LabelledSelect).results():
            wanted = self._select_value(view, str(select.id or ""))
            if select.value != wanted:
                # Setting a value fires Changed, which would loop straight back
                # here through apply_view().
                with select.prevent(Select.Changed):
                    select.value = wanted

    @staticmethod
    def _select_value(view: BoardView, widget_id: str) -> object:
        """What a dropdown should be showing, given the current view."""
        if widget_id == STATE_SELECT:
            states = view.card_filter.states
            return states[0].value if states else Select.NULL
        if widget_id == SORT_SELECT:
            return view.sort.value
        if widget_id.startswith(PROVIDER_SELECT_PREFIX):
            field = widget_id.removeprefix(PROVIDER_SELECT_PREFIX)
            return view.card_filter.provider.get(field, Select.NULL)
        if widget_id == PROJECT_SELECT:
            return view.card_filter.project or Select.NULL
        if widget_id == STATUS_SELECT:
            column = view.card_filter.column_id
            return str(column) if column is not None else Select.NULL
        return Select.NULL

    @staticmethod
    def _chip_active(view: BoardView, action: Action) -> bool:
        """Whether a chip is currently lit."""
        match action.kind:
            case ActionKind.STATE:
                return action.enum(FilterState) in view.card_filter.states
            case ActionKind.SORT:
                return view.sort is action.enum(SortKey)
            case ActionKind.PICK_PROVIDER:
                return action.pair[0] in view.card_filter.provider
            case ActionKind.ACT:
                return action.enum(Act) is Act.REVERSE and view.reverse
            case _:
                return False

    def set_search(self, text: str) -> None:
        field = self.query_one("#bar-search", Input)
        if field.value != text:
            with field.prevent(Input.Changed):
                field.value = text

    # ---- input ---------------------------------------------------------

    def _post(self, kind: ActionKind, value: object = "") -> None:
        self.post_message(self.ChipPressed(Action.of(kind, value)))

    @on(Click, "#bar-caret")
    def caret_clicked(self, event: Click) -> None:
        event.stop()
        self.cycle()

    @on(Click, "#bar-home")
    def home_clicked(self, event: Click) -> None:
        event.stop()
        self._post(ActionKind.LAYOUT, BoardLayout.KANBAN)

    @on(Click, ".bar-label")
    def label_clicked(self, event: Click) -> None:
        event.stop()
        label = event.widget
        if label is None or label.id is None:
            return
        try:
            menu = Menu(label.id.removeprefix("bar-menu-"))
        except ValueError:
            return
        self.post_message(self.MenuRequested(menu, label.region.x, label.region.y + 1))

    @on(Click, ".chip")
    def chip_clicked(self, event: Click) -> None:
        event.stop()
        chip = event.widget
        if chip is None or chip.id is None:
            return
        action = Action.from_chip_id(chip.id)
        if action is not None:
            self.post_message(self.ChipPressed(action))

    @on(Input.Changed, "#bar-search")
    def search_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.SearchChanged(event.value))

    @on(Input.Changed, "#filter-key")
    def key_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._post(ActionKind.KEY, event.value)

    @on(Input.Changed, "#filter-created-from")
    def created_from_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._post(ActionKind.FROM, event.value)

    @on(Input.Changed, "#filter-created-until")
    def created_until_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._post(ActionKind.UNTIL, event.value)

    @on(Input.Changed, "#filter-query")
    def query_changed(self, event: Input.Changed) -> None:
        # A query runs on the server, so it waits for Search rather than
        # re-running on every keystroke.
        event.stop()

    @on(Input.Submitted, "#filter-query")
    def query_submitted(self, event: Input.Submitted) -> None:
        """Enter in the query field is the same explicit action as Search."""
        event.stop()
        self._post(ActionKind.RUN, event.value)

    @on(Checkbox.Changed, "#filter-sprint")
    def sprint_toggled(self, event: Checkbox.Changed) -> None:
        event.stop()
        self._post(ActionKind.SPRINT, int(event.value))

    @on(Button.Pressed, "#filter-search")
    def search_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        query = self.query("#filter-query")
        self._post(ActionKind.RUN, str(query.first(Input).value) if query else "")

    @on(Select.Changed)
    def select_changed(self, event: Select.Changed) -> None:
        event.stop()
        widget_id = str(event.select.id or "")
        value = "" if event.value is Select.NULL else str(event.value)

        if widget_id == STATE_SELECT:
            self._post(ActionKind.PICK_STATE, value)
        elif widget_id == SORT_SELECT:
            self._post(ActionKind.SORT, value or SortKey.MANUAL)
        elif widget_id == SAVED_SELECT and value:
            self._post(ActionKind.SAVED, value)
        elif widget_id.startswith(PROVIDER_SELECT_PREFIX):
            field = widget_id.removeprefix(PROVIDER_SELECT_PREFIX)
            self._post(ActionKind.PICK_PROVIDER, f"{field}={value}")
        elif widget_id == PROJECT_SELECT:
            self._post(ActionKind.PROJECT, value)
        elif widget_id == STATUS_SELECT:
            self._post(ActionKind.STATUS, value)
