"""Reload and provider-sync orchestration for the TUI."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from pykantui.i18n import translate as _
from pykantui.pages.sync import SyncChoice, SyncConfirmScreen, SyncDecision, SyncProgressScreen
from pykantui.sync.provider import PostSyncReloadError
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


def _sidebar_edit_is_open(app: KanbanApp) -> bool:
    """Keep reload/sync actions from replacing a recoverable inline draft."""
    view = app.query_one(WorkItemsView)
    if not view.editing:
        return False
    app.notify(
        f"{_('Save locally')} · {_('Cancel')}",
        title=_("Edit"),
        severity="warning",
        timeout=3,
    )
    return True


class SyncController:
    """Keep network and reload workflows out of the application shell."""

    async def action_toggle_team(self) -> None:
        """Show cached team cards alongside the current user's cards."""
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False):
            return
        if _sidebar_edit_is_open(app):
            return
        backend = app.backend
        reload_board = getattr(backend, "reload", None)
        if not hasattr(backend, "show_team") or reload_board is None:
            app.notify(_("this board already shows everything"), timeout=3)
            return

        backend.show_team = not backend.show_team
        try:
            reload_board()
        except (OSError, ValueError) as error:
            backend.show_team = not backend.show_team
            app.notify(
                _("could not load the team's cards: {error}").format(error=error), severity="error", timeout=6
            )
            return

        await app.board.refresh_board()
        app.query_one(WorkItemsView).refresh_tasks()
        app.notify(
            _("showing the whole project — their cards are read-only")
            if backend.show_team
            else _("showing only your cards"),
            timeout=3,
        )

    async def action_refresh_board(self) -> None:
        """Reload the local board shape and cards without losing the TUI."""
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False):
            return
        if _sidebar_edit_is_open(app):
            return
        before = [column.column_id for column in app.visible_columns]
        try:
            app.backend.reload_local()
        except (OSError, ValueError) as error:
            app.notify(_("Reload failed: {error}").format(error=error), severity="error", timeout=6)
            return
        after = [column.column_id for column in app.visible_columns]

        if before != after:
            await app.apply_view()
            app.notify(_("Columns reloaded"), timeout=2)
            return
        await app.board.refresh_board()
        app.query_one(WorkItemsView).refresh_tasks()
        app.notify(_("Reloaded local files"), timeout=2)

    def action_sync_board(self) -> None:
        """Start one sync without allowing key repeat to cancel its thread."""
        app = cast("KanbanApp", self)
        if getattr(app, "_sync_in_flight", False) or isinstance(app.screen, SyncProgressScreen):
            return
        if not app.backend.supports_sync:
            app.notify(_("This local board has no provider to sync"), timeout=3)
            return
        if getattr(app.backend, "plan_sync", None) is None or getattr(app.backend, "sync_now", None) is None:
            app.notify(_("This backend cannot sync"), severity="error", timeout=4)
            return
        app._sync_in_flight = True
        app.run_worker(self._run_sync_board(), group="provider-sync", exclusive=False)

    async def _run_sync_board(self) -> None:
        """Preview provider writes, ask once, then reconcile both sides."""
        app = cast("KanbanApp", self)
        try:
            await self._sync_board_flow(app)
        finally:
            app._sync_in_flight = False

    async def _sync_board_flow(self, app: KanbanApp) -> None:
        if _sidebar_edit_is_open(app):
            return
        if not app.backend.supports_sync:
            app.notify(_("This local board has no provider to sync"), timeout=3)
            return

        plan_sync = getattr(app.backend, "plan_sync", None)
        sync_now = getattr(app.backend, "sync_now", None)
        if plan_sync is None or sync_now is None:
            app.notify(_("This backend cannot sync"), severity="error", timeout=4)
            return

        progress_screen = SyncProgressScreen(app.backend.display_kind(), app.backend.get_active_board().name)
        await app.push_screen(progress_screen)
        try:
            plan = await asyncio.to_thread(plan_sync)
        except Exception as error:  # provider boundaries normalise their own errors
            progress_screen.finish_error(_("Sync preview failed: {error}").format(error=error))
            return

        if app.screen is progress_screen:
            await app.pop_screen()

        decision = SyncDecision(SyncChoice.PULL)
        if not plan.is_empty():
            decision = await app.push_screen_wait(
                SyncConfirmScreen(app.backend.display_kind(), app.backend.get_active_board().name, plan)
            )
        choice = decision.choice
        if choice is SyncChoice.CANCEL:
            app.notify(_("Sync cancelled — local changes kept"), timeout=3)
            return

        progress_screen = SyncProgressScreen(app.backend.display_kind(), app.backend.get_active_board().name)
        await app.push_screen(progress_screen)

        def show_running_progress(update: SyncProgressUpdate) -> None:
            """Keep Close locked until sync_now and its backend cleanup return."""
            if not update.active:
                update = replace(
                    update,
                    phase=SyncPhase.FINALIZING,
                    active=True,
                    error=False,
                )
            progress_screen.update_progress(update)

        try:
            report = await asyncio.to_thread(
                sync_now,
                confirm=(lambda _plan: False) if choice is SyncChoice.PULL else (lambda _plan: True),
                expected_plan=plan,
                push_edits=choice is not SyncChoice.PULL,
                push_conflicts=choice is SyncChoice.FORCE,
                accept_remote_conflicts=choice is SyncChoice.USE_PROVIDER,
                conflict_resolutions=decision.conflicts,
                progress=show_running_progress,
            )
        except PostSyncReloadError as error:
            progress_screen.finish_local_reload_error(error.report, str(error.reload_error))
            return
        except Exception as error:
            progress_screen.finish_error(_("Sync failed: {error}").format(error=error))
            return

        progress_screen.begin_board_refresh()
        # Let the user see the final local refresh phase before potentially
        # large widget work. Close remains locked until every refresh returns.
        await asyncio.sleep(0)
        try:
            await app.board.refresh_board()
            app.query_one(WorkItemsView).refresh_tasks()
            app.refresh_view()
        except Exception as error:
            progress_screen.finish_refresh_error(report, str(error))
            return
        progress_screen.finish_success(report)
