"""Build the grouped command tree without growing the application class."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import partial

from textual.app import SystemCommand

from pykantui.core.actions import Act, Action, ActionKind, Menu
from pykantui.i18n import translate as _
from pykantui.pages.grouped_palette import PaletteCommand, PaletteGroup, PaletteNode
from pykantui.pages.menu import MenuItem


def build_palette_tree(
    *,
    display_kind: str,
    menu_items: Callable[[Menu], list[MenuItem]],
    main_title: Callable[[Action, str], str],
    run_action: Callable[[str], object],
    system_commands: Iterable[SystemCommand],
) -> tuple[PaletteNode, ...]:
    """Build one provider-aware palette tree from the existing menu actions."""

    def command(item: MenuItem, description: str) -> PaletteCommand:
        action = Action.parse(item.key)
        label = main_title(action, item.label) if action is not None else item.label
        if action is not None and action.kind is ActionKind.ACT and action.enum(Act) is Act.SYNC:
            label = label.removesuffix("…")
        return PaletteCommand(
            command_id=item.key,
            label=label,
            description=description,
            callback=partial(run_action, item.key),
        )

    main_items = menu_items(Menu.MAIN)
    board_descriptions = {
        ActionKind.LAYOUT: _("Return to the Kanban home board"),
        ActionKind.ACT: _("Create a card or reload Markdown files"),
    }
    board_children = tuple(
        command(item, board_descriptions[action.kind])
        for item in main_items
        if (action := Action.parse(item.key)) is not None
        and (
            action.kind is ActionKind.LAYOUT
            or (
                action.kind is ActionKind.ACT
                and action.enum(Act) in {Act.NEW, Act.REFRESH, Act.PROJECTS}
            )
        )
    )

    menu_descriptions = {
        Menu.FILTER: _("State, provider fields, and saved filters"),
        Menu.SORT: _("Manual, title, due date, age, or reverse"),
        Menu.COLUMNS: _("Add, rename, hide, delete, or expand"),
        Menu.VIEW: _("Kanban, Split, Rows, and board behavior"),
    }
    leaf_descriptions = {
        Menu.FILTER: _("Change the filters applied to visible cards"),
        Menu.SORT: _("Change the order of visible cards"),
        Menu.COLUMNS: _("Change the current board columns"),
        Menu.VIEW: _("Change the layout or board behavior"),
        Menu.HELP: _("Open this pykantui help topic"),
    }

    def grouped_menu_children(menu: Menu) -> tuple[PaletteNode, ...]:
        items = tuple((action, item) for item in menu_items(menu) if (action := Action.parse(item.key)) is not None)
        if menu is Menu.FILTER:
            groups: list[PaletteNode] = []
            filter_groups = (
                (ActionKind.STATE, "state", _("State"), _("Blocked, overdue, due dates, and local notes")),
                (
                    ActionKind.FOCUS,
                    "provider-fields",
                    _("Provider fields"),
                    _("Filters supported by {provider}").format(provider=display_kind),
                ),
                (
                    ActionKind.SAVED,
                    "saved-filters",
                    _("Saved filters"),
                    _("Apply a saved combination of filters"),
                ),
            )
            for kind, group_id, label, description in filter_groups:
                children = tuple(
                    command(item, leaf_descriptions[menu]) for action, item in items if action.kind is kind
                )
                if children:
                    groups.append(
                        PaletteGroup(
                            group_id=f"organize-filter-{group_id}",
                            label=label,
                            description=description,
                            children=children,
                        )
                    )
            groups.extend(
                command(item, leaf_descriptions[menu]) for action, item in items if action.kind is ActionKind.ACT
            )
            return tuple(groups)

        if menu is Menu.VIEW:
            groups = []
            view_groups = (
                (ActionKind.LAYOUT, "layout", _("Layout"), _("Kanban, Split, or Rows")),
                (ActionKind.PANE, "split-pane", _("Split pane"), _("Resize or reset the work-items divider")),
                (ActionKind.VIEW, "behavior", _("Behavior"), _("Movement, confirmation, detail, and columns")),
            )
            for kind, group_id, label, description in view_groups:
                children = tuple(
                    command(item, leaf_descriptions[menu]) for action, item in items if action.kind is kind
                )
                if children:
                    groups.append(
                        PaletteGroup(
                            group_id=f"organize-view-{group_id}",
                            label=label,
                            description=description,
                            children=children,
                        )
                    )
            return tuple(groups)

        return tuple(command(item, leaf_descriptions[menu]) for _, item in items)

    organize_children = tuple(
        PaletteGroup(
            group_id=f"organize-{menu.value}",
            label=_(menu.value.title()),
            description=menu_descriptions[menu],
            children=grouped_menu_children(menu),
        )
        for menu in (Menu.FILTER, Menu.SORT, Menu.COLUMNS, Menu.VIEW)
    )

    roots: list[PaletteNode] = [
        PaletteGroup(
            group_id="board",
            label=_("Board"),
            description=_("Home, create cards, and reload local files"),
            children=board_children,
        ),
        PaletteGroup(
            group_id="organize",
            label=_("Organize"),
            description=_("Filter, sort, columns, and layouts"),
            children=organize_children,
        ),
    ]

    sync_item = next(
        (
            item
            for item in main_items
            if (action := Action.parse(item.key)) is not None
            and action.kind is ActionKind.ACT
            and action.enum(Act) is Act.SYNC
        ),
        None,
    )
    if sync_item is not None:
        roots.append(command(sync_item, _("Review local and provider changes before syncing")))

    roots.append(
        PaletteGroup(
            group_id="help",
            label=_("Help"),
            description=_("Keys and where pykantui stores data"),
            children=tuple(command(item, leaf_descriptions[Menu.HELP]) for item in menu_items(Menu.HELP)),
        )
    )
    roots.append(
        PaletteGroup(
            group_id="system",
            label=_("System"),
            description=_("Theme, screenshot, maximize, and application commands"),
            children=tuple(
                PaletteCommand(
                    command_id=f"system-{index}",
                    label=item.title,
                    description=item.help,
                    callback=item.callback,
                )
                for index, item in enumerate(sorted(system_commands, key=lambda item: item.title))
            ),
        )
    )
    return tuple(roots)
