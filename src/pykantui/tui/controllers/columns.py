"""Column context-menu and mutation orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual import work

from pykantui.config import BoardConfig
from pykantui.core.actions import ColumnCommand
from pykantui.i18n import translate as _
from pykantui.pages.menu import ContextMenuScreen, MenuItem, PromptScreen
from pykantui.tui.widgets.board import KanbanBoard
from pykantui.tui.widgets.column import BoardColumn

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class ColumnController:
    """Own column menus while delegating persistence to the backend."""

    @work
    async def on_kanban_board_column_menu_requested(self, event: KanbanBoard.ColumnMenuRequested) -> None:
        app = cast("KanbanApp", self)
        if isinstance(app.screen, ContextMenuScreen):
            return
        column = event.column
        config = app.backend.board_config()

        toggle = ColumnCommand.EXPAND if column.collapsed else ColumnCommand.COLLAPSE
        items = [MenuItem(toggle.value, _(toggle.value.title()))]
        if app.backend.can_create_tasks():
            items.insert(0, MenuItem(ColumnCommand.NEW.value, _("New card here")))
        if config is not None:
            items += [
                MenuItem(ColumnCommand.RENAME.value, _("Rename column…")),
                MenuItem(ColumnCommand.ADD_AFTER.value, _("Add column after…")),
                MenuItem(ColumnCommand.HIDE.value, _("Hide column")),
            ]
        if app.backend.can_delete_tasks():
            items.append(MenuItem(ColumnCommand.CLEAR.value, _("Delete all cards…")))
        if config is not None:
            items.append(MenuItem(ColumnCommand.DELETE.value, _("Delete column…")))

        chosen = await app.push_screen_wait(ContextMenuScreen(column.title, items, anchor_at=event.anchor))
        if chosen is None:
            return
        try:
            command = ColumnCommand(chosen)
        except ValueError:
            return
        await app._run_column_action(command, column, config)

    async def _run_column_action(
        self,
        command: ColumnCommand,
        column: BoardColumn,
        config: BoardConfig | None,
    ) -> None:
        app = cast("KanbanApp", self)
        board = app.board

        match command:
            case ColumnCommand.COLLAPSE | ColumnCommand.EXPAND:
                board.set_collapsed(column, command is ColumnCommand.COLLAPSE)
                return
            case ColumnCommand.EXPAND_ALL:
                board.action_expand_all()
                return
            case ColumnCommand.NEW:
                app.push_screen(app._edit_screen(column.column_id), callback=app._create_task)
                return
            case ColumnCommand.CLEAR:
                await app._clear_column(column)
                return
            case _:
                pass

        if config is None:
            return
        entry = config.find(column.column_id)
        if entry is None:
            return

        match command:
            case ColumnCommand.RENAME:
                name = await app.push_screen_wait(PromptScreen(_("Rename column"), value=entry.name))
                if name is None or name == entry.name:
                    return
                entry.name = name
            case ColumnCommand.ADD_AFTER:
                name = await app.push_screen_wait(PromptScreen(_("New column name"), placeholder=_("Blocked")))
                if name is None:
                    return
                if config.find_by_name(name) is not None:
                    app.notify(
                        _("There is already a column called {name}").format(name=name), severity="warning", timeout=4
                    )
                    return
                config.add(name, after=entry)
            case ColumnCommand.HIDE:
                if sum(1 for item in config.columns if item.visible) == 1:
                    app.notify(_("At least one column has to stay visible"), severity="warning", timeout=4)
                    return
                entry.visible = False
            case ColumnCommand.DELETE:
                question = _("Delete the {column} column?").format(column=entry.name)
                if not await app._confirm(question, _("Delete column")):
                    return
                if len(config.columns) == 1:
                    app.notify(_("A board needs at least one column"), severity="warning", timeout=4)
                    return
                destination = next(item for item in config.ordered() if item.column_id != entry.column_id)
                app._rehome(entry.column_id, destination.column_id)
                config.remove(entry)

        config.save()
        app.backend.reload_config()
        await board.rebuild()

    async def _clear_column(self, column: BoardColumn) -> None:
        app = cast("KanbanApp", self)
        doomed = [task for task in app.backend.get_tasks() if task.column_id == column.column_id]
        if not doomed:
            app.notify(_("{column} is already empty").format(column=column.title), timeout=3)
            return
        question = _("Delete {count} card(s) from {column}?").format(count=len(doomed), column=column.title)
        if not await app._confirm(question, _("Delete cards")):
            return
        for task in doomed:
            app.backend.delete_task(task.task_id)
        await app.board.rebuild()

    async def _confirm(self, question: str, verb: str) -> bool:
        app = cast("KanbanApp", self)
        chosen = await app.push_screen_wait(
            ContextMenuScreen(question, [MenuItem("yes", verb), MenuItem("no", _("Cancel"))])
        )
        return chosen == "yes"

    def _rehome(self, from_column: int, to_column: int) -> None:
        app = cast("KanbanApp", self)
        for task in app.backend.get_tasks():
            if task.column_id == from_column:
                task.column_id = to_column
                app.backend.update_task(task)
