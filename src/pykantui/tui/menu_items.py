"""Pure construction of provider-aware application menu rows."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

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
from pykantui.core.filters import SORT_LABELS, STATE_LABELS, BoardView, CardFilter
from pykantui.core.work_items import OPTIONAL_WORK_ITEM_COLUMNS, WORK_ITEM_COLUMN_SPECS, WorkItemColumn
from pykantui.i18n import translate as _
from pykantui.models import BoardLayout, MovementMode
from pykantui.pages.menu import MenuItem
from pykantui.tracker.filter_fields import FilterFieldName, FilterFieldSpec


def build_menu_items(
    menu: Menu,
    *,
    view: BoardView,
    board_layout: BoardLayout,
    movement_mode: MovementMode,
    confirm_moves: bool,
    supports_sync: bool,
    provider_fields: Sequence[FilterFieldSpec],
    saved_filters: Mapping[str, CardFilter],
    filter_prefix: str,
    available_columns: Collection[WorkItemColumn] = (),
) -> list[MenuItem]:
    """Build one menu without depending on Textual application state."""
    tick = "✓ "
    blank = "  "

    def row(
        kind: ActionKind,
        value: object,
        label: str,
        *,
        on: bool = False,
    ) -> MenuItem:
        prefix = tick if on else blank
        return MenuItem(Action.of(kind, value).encode(), prefix + label)

    match menu:
        case Menu.MAIN:
            items = [
                MenuItem(
                    Action.of(ActionKind.LAYOUT, BoardLayout.KANBAN).encode(),
                    _("⌂ Home · ▥ Kanban"),
                )
            ]
            items.extend(
                MenuItem(Action.of(ActionKind.OPEN, target).encode(), f"{target.label}…")
                for target in (Menu.FILTER, Menu.SORT, Menu.COLUMNS, Menu.VIEW)
            )
            items.extend(
                (
                    MenuItem(Action.of(ActionKind.ACT, Act.NEW).encode(), _("New card")),
                    MenuItem(
                        Action.of(ActionKind.ACT, Act.REFRESH).encode(),
                        _("Reload files"),
                    ),
                    MenuItem(Action.of(ActionKind.ACT, Act.PROJECTS).encode(), _("Projects…")),
                )
            )
            if supports_sync:
                items.append(
                    MenuItem(
                        Action.of(ActionKind.ACT, Act.SYNC).encode(),
                        _("Sync with provider…"),
                    )
                )
            items.append(MenuItem(Action.of(ActionKind.OPEN, Menu.HELP).encode(), _("Help")))
            return items
        case Menu.FILTER:
            items = [
                row(ActionKind.STATE, state, _(label), on=state in view.card_filter.states)
                for state, label in STATE_LABELS.items()
            ]
            metadata_fields = {
                FilterFieldName.ASSIGNEE,
                FilterFieldName.ISSUE_TYPE,
                FilterFieldName.PRIORITY,
                FilterFieldName.LABELS,
            }
            items.extend(
                row(
                    ActionKind.FOCUS,
                    f"{filter_prefix}provider-{field.name.value}",
                    f"{_(field.label)}…",
                )
                for field in provider_fields
                if field.name in metadata_fields
            )
            items.extend(
                row(ActionKind.SAVED, name, f"★ {name}") for name in saved_filters
            )
            items.extend(
                (
                    row(ActionKind.ACT, Act.SAVE, _("+ Save this filter…")),
                    row(ActionKind.ACT, Act.CLEAR, _("Clear filters")),
                )
            )
            return items
        case Menu.SORT:
            items = [
                row(ActionKind.SORT, key, _(label), on=view.sort is key)
                for key, label in SORT_LABELS.items()
            ]
            items.append(
                row(ActionKind.ACT, Act.REVERSE, _("⇵ Reverse"), on=view.reverse)
            )
            return items
        case Menu.COLUMNS:
            if board_layout in {BoardLayout.ROWS, BoardLayout.SPLIT}:
                supported = set(available_columns)
                return [
                    row(
                        ActionKind.TABLE_COLUMN,
                        column,
                        _(WORK_ITEM_COLUMN_SPECS[column].label),
                        on=column in view.columns,
                    )
                    for column in OPTIONAL_WORK_ITEM_COLUMNS
                    if column in supported
                ]
            return [
                row(ActionKind.COL, ColumnCommand.ADD_AFTER, _("Add column…")),
                row(ActionKind.COL, ColumnCommand.RENAME, _("Rename this column…")),
                row(ActionKind.COL, ColumnCommand.HIDE, _("Hide this column")),
                row(ActionKind.COL, ColumnCommand.DELETE, _("Delete this column…")),
                row(ActionKind.COL, ColumnCommand.EXPAND_ALL, _("Expand all columns")),
            ]
        case Menu.VIEW:
            layouts = (BoardLayout.KANBAN, BoardLayout.SPLIT, BoardLayout.ROWS)
            items = [
                row(
                    ActionKind.LAYOUT,
                    layout,
                    layout.label,
                    on=board_layout is layout,
                )
                for layout in layouts
            ]
            if board_layout is BoardLayout.SPLIT:
                items.extend(
                    (
                        row(ActionKind.PANE, PaneAdjustment.NARROWER, _("[ Narrow work items")),
                        row(ActionKind.PANE, PaneAdjustment.WIDER, _("] Widen work items")),
                        row(ActionKind.PANE, PaneAdjustment.RESET, _("\\ Reset divider")),
                    )
                )
            items.extend(
                (
                    row(
                        ActionKind.VIEW,
                        ViewToggle.MOVEMENT,
                        _("Movement: {mode}").format(mode=_(str(movement_mode))),
                    ),
                    row(
                        ActionKind.VIEW,
                        ViewToggle.CONFIRM,
                        _("Confirm moves"),
                        on=confirm_moves,
                    ),
                    row(ActionKind.VIEW, ViewToggle.DETAIL, _("Card detail")),
                    row(
                        ActionKind.VIEW,
                        ViewToggle.COLLAPSE,
                        _("Collapse this column"),
                    ),
                )
            )
            return items
        case Menu.HELP:
            return [
                MenuItem(Action.of(ActionKind.HELP, HelpTopic.KEYS).encode(), _("Keys")),
                MenuItem(
                    Action.of(ActionKind.HELP, HelpTopic.WHERE).encode(),
                    _("Where things are stored"),
                ),
            ]


__all__ = ["build_menu_items"]
