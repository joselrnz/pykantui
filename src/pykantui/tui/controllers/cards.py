"""Card create, detail, row-menu, and sidebar-save orchestration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from textual import work

from pykantui.core.actions import WorkItemCommand
from pykantui.i18n import translate as _
from pykantui.models import MoveResult, Task, same_task_identity
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.edit import TaskEditScreen
from pykantui.pages.menu import ContextMenuScreen, MenuItem
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.card_fields import EditorPolicy
from pykantui.tui.widgets.comments import CommentsPane
from pykantui.tui.widgets.work_items import WorkItemsView

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class CardController:
    """Coordinate card screens and local-first writes for every board view."""

    _row_menu_open = False
    _card_popup_open = False

    def action_new_task(self) -> None:
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False):
            return
        if not app.backend.can_create_tasks():
            app.notify(
                _("{provider} cannot create cards here").format(provider=app.backend.display_kind()),
                severity="warning",
                timeout=3,
            )
            return

        columns = app.visible_columns
        if not columns:
            return
        app.push_screen(app._edit_screen(columns[0].column_id), callback=app._create_task)

    def _edit_screen(self, column_id: int) -> TaskEditScreen:
        app = cast("KanbanApp", self)
        return TaskEditScreen(
            column_id=column_id,
            task_id=app.backend.next_task_id(),
            columns=app.column_choices(),
            policy=app._editor_policy(create=True),
            available_fields=app.backend.available_task_fields(),
            status_group=app.backend.column_group(column_id),
            status_groups={
                column.column_id: app.backend.column_group(column.column_id)
                for column in app.visible_columns
            },
        )

    def _editor_policy(self, *, create: bool = False) -> EditorPolicy:
        """Return one immutable description of what the editor may do."""
        app = cast("KanbanApp", self)
        return EditorPolicy(
            provider_name=app.backend.display_kind() if app.backend.supports_sync else "",
            writable_fields=(
                app.backend.creatable_task_fields() if create else app.backend.editable_task_fields()
            ),
            private_notes=app.backend.supports_private_notes(),
            local_first=app.backend.supports_sync,
        )

    def editor_policy(self) -> EditorPolicy:
        """Return the read/edit policy shared with embedded details."""
        return self._editor_policy()

    def _create_task(self, task: Task | None) -> None:
        app = cast("KanbanApp", self)
        if task is None:
            return
        result = app.backend.create_task(task)
        if not result.ok:
            app.notify(result.message, title=_("Create failed"), severity="error", timeout=5)
            return
        app.notify(result.message, title=_("Saved locally"), timeout=4)
        app.call_later(app._reload)

    async def _reload(self) -> None:
        app = cast("KanbanApp", self)
        await app.board.refresh_board()
        app.query_one(WorkItemsView).refresh_tasks()

    def on_task_card_detail_requested(self, event: TaskCard.DetailRequested) -> None:
        app = cast("KanbanApp", self)
        app._open_card(event.card.task_, editing=False)

    def on_work_items_view_detail_requested(self, event: WorkItemsView.DetailRequested) -> None:
        app = cast("KanbanApp", self)
        if event.editing and not app.backend.can_edit_task(event.task):
            app.notify(
                _("{provider} cards cannot be edited here").format(provider=app.backend.display_kind()),
                severity="warning",
                timeout=3,
            )
            return
        app._open_card(event.task, editing=event.editing)

    @work
    async def on_work_items_view_row_menu_requested(self, event: WorkItemsView.RowMenuRequested) -> None:
        """Open a compact row menu and preserve Split's inline edit contract."""
        app = cast("KanbanApp", self)
        if app._row_menu_open or not event.view.display:
            return
        current = app._current_task(event.task)
        if current is None:
            return
        app._row_menu_open = True
        try:
            items = [MenuItem(WorkItemCommand.VIEW, _("View"))]
            if app.backend.can_edit_task(current):
                items.append(MenuItem(WorkItemCommand.EDIT, _("Edit")))
            chosen = await app.push_screen_wait(
                ContextMenuScreen(_("Work Items"), items, anchor_at=event.anchor)
            )
        finally:
            app._row_menu_open = False
        if chosen is None:
            return
        try:
            command = WorkItemCommand(chosen)
        except ValueError:
            return
        current = app._current_task(event.task)
        if current is None or not event.view.display:
            return
        if command is WorkItemCommand.EDIT and event.view.detail_visible:
            await event.view.start_inline_edit(current)
            return
        app._open_card(current, editing=command is WorkItemCommand.EDIT)

    async def on_work_items_view_save_requested(self, event: WorkItemsView.SaveRequested) -> None:
        """Persist an inline Split edit locally without opening another screen."""
        app = cast("KanbanApp", self)
        result = app.backend.update_task(event.task)
        if not result.ok:
            event.view.inline_save_failed()
            app.notify(result.message, title=_("Save failed"), severity="error", timeout=5)
            return
        await app.apply_view()
        await event.view.inline_save_succeeded()
        app.notify(result.message, title=_("Saved locally"), timeout=4)

    @work
    async def _open_card(self, task: Task, *, editing: bool) -> None:
        """Open the one card popup, either to read or to edit."""
        app = cast("KanbanApp", self)
        if app._card_popup_open:
            return
        current = app._current_task(task)
        if current is None:
            return
        if editing and not app.backend.can_edit_task(current):
            app.notify(
                _("{provider} cards cannot be edited here").format(provider=app.backend.display_kind()),
                severity="warning",
                timeout=3,
            )
            return
        app._card_popup_open = True
        try:
            column = next((item for item in app.visible_columns if item.column_id == current.column_id), None)
            edited = await app.push_screen_wait(
                TaskDetailScreen(
                    task=current,
                    column_name=column.name if column else str(current.column_id),
                    blockers=app.backend.get_tasks_by_ids(current.blocked_by),
                    blocking=app.backend.get_tasks_by_ids(current.blocking),
                    columns=app.column_choices(),
                    priorities=app.provider_values("priority"),
                    writable=app.backend.can_edit_task(current),
                    editing=editing,
                    policy=app._editor_policy(),
                    available_fields=app.backend.available_task_fields(),
                    status_group=app.backend.column_group(current.column_id),
                    status_groups={
                        item.column_id: app.backend.column_group(item.column_id)
                        for item in app.visible_columns
                    },
                    comments_backend=app.backend,
                )
            )
            if edited is None:
                return
            result = app.backend.update_task(edited)
            if not result.ok:
                app.notify(result.message, title=_("Save failed"), severity="error", timeout=5)
                return
            await app.apply_view()
            app.notify(result.message, title=_("Saved locally"), timeout=4)
        finally:
            app._card_popup_open = False

    def _current_task(self, snapshot: Task) -> Task | None:
        """Resolve a queued UI action without trusting a stale row number."""
        app = cast("KanbanApp", self)
        current = app.backend.get_task_by_id(snapshot.task_id)
        if current is None or not same_task_identity(snapshot, current):
            return None
        return current

    @work
    async def on_comments_pane_save_requested(self, event: CommentsPane.SaveRequested) -> None:
        """Persist a comment draft locally without calling a provider client."""
        app = cast("KanbanApp", self)
        current = app._current_task(event.task)
        if current is None:
            event.pane.save_failed(event.task, _("The selected card changed; review the draft and try again"))
            return
        try:
            result = await asyncio.to_thread(app.backend.save_comment_draft, current, event.body)
        except Exception as error:  # backend boundary; retain the user's text
            event.pane.save_failed(current, str(error))
            app.notify(str(error), title=_("Save failed"), severity="error", timeout=5)
            return
        if not result.ok:
            event.pane.save_failed(current, result.message)
            app.notify(result.message, title=_("Save failed"), severity="error", timeout=5)
            return
        event.pane.save_succeeded(current)
        app.notify(result.message, title=_("Saved locally"), timeout=4)

    @work
    async def on_comments_pane_refresh_requested(self, event: CommentsPane.RefreshRequested) -> None:
        """Refresh provider comments only through an explicit backend callback."""
        app = cast("KanbanApp", self)
        current = app._current_task(event.task)
        if current is None:
            event.pane.refresh_failed(event.task, _("The selected card changed; refresh it and try again"))
            return
        refresh = getattr(app.backend, "refresh_task_comments", None)
        if not callable(refresh):
            event.pane.refresh_failed(current, _("Comment refresh is unavailable"))
            return
        try:
            result = await asyncio.to_thread(refresh, current)
        except Exception as error:  # optional backend boundary
            event.pane.refresh_failed(current, str(error))
            app.notify(str(error), title=_("Refresh failed"), severity="error", timeout=5)
            return
        if isinstance(result, MoveResult) and not result.ok:
            event.pane.refresh_failed(current, result.message)
            app.notify(result.message, title=_("Refresh failed"), severity="error", timeout=5)
            return
        event.pane.refresh_succeeded(current)

    def on_task_card_edit_requested(self, event: TaskCard.EditRequested) -> None:
        app = cast("KanbanApp", self)
        if not app.backend.can_edit_task(event.card.task_):
            app.notify(
                _("{provider} cards cannot be edited here").format(provider=app.backend.display_kind()),
                severity="warning",
                timeout=3,
            )
            return
        app._open_card(event.card.task_, editing=True)
