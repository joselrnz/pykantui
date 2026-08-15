"""Uncommon lifecycle edges around the observable F5 sync flow.

The primary happy path lives in ``test_sync_progress_ui``.  This module keeps
the less common state-machine combinations separate so regressions are easy to
name: preview retries, key-repeat races, stale callbacks, hostile display text,
tiny terminals, and failures after the provider has already succeeded.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import unittest
import weakref
from collections.abc import Callable
from typing import cast
from unittest.mock import patch

from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.pilot import Pilot
from textual.widgets import Button, Label, LoadingIndicator

from pykantui.pages.grouped_palette import GroupedCommandPalette
from pykantui.pages.sync import SyncConfirmScreen, SyncProgressScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.sync.provider import PostSyncReloadError
from pykantui.tracker.models import IssueEdit, RemoteIssue
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.models import InvalidCard, PendingPush, SyncPlan, SyncReport
from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate, emit_progress


def _label(screen: SyncProgressScreen, selector: str) -> str:
    return str(screen.query_one(selector, Label).render())


class EdgeSyncBackend(JsonBackend):
    """Controllable provider-shaped backend for lifecycle integration tests."""

    supports_sync = True

    def __init__(
        self,
        *,
        plan: SyncPlan | None = None,
        report: SyncReport | None = None,
        preview_failures: int = 0,
        block_preview: bool = False,
        block_sync: bool = False,
    ) -> None:
        super().__init__()
        self.plan = plan or SyncPlan()
        self.report = report or SyncReport(written=["EDGE-1"])
        self.preview_failures = preview_failures
        self.block_preview = block_preview
        self.block_sync = block_sync
        self.plan_calls = 0
        self.sync_calls = 0
        self.sync_options: list[dict[str, object]] = []
        self.preview_started = threading.Event()
        self.preview_release = threading.Event()
        self.sync_started = threading.Event()
        self.sync_release = threading.Event()
        self.reload_local_calls = 0
        self.team_reload_calls = 0
        self.show_team = False

    def display_kind(self) -> str:
        return "Edge provider"

    def plan_sync(self) -> SyncPlan:
        self.plan_calls += 1
        self.preview_started.set()
        if self.block_preview and not self.preview_release.wait(timeout=10):
            raise TimeoutError("test did not release preview")
        if self.preview_failures:
            self.preview_failures -= 1
            raise RuntimeError("preview endpoint unavailable")
        return self.plan

    def sync_now(
        self,
        *,
        progress: Callable[[SyncProgressUpdate], None] | None = None,
        **options: object,
    ) -> SyncReport:
        self.sync_calls += 1
        self.sync_options.append(options)
        emit_progress(
            progress,
            SyncPhase.RECONCILING,
            completed=2,
            total=3,
            item="EDGE-2",
            summary="Reconciling cards",
        )
        self.sync_started.set()
        if self.block_sync and not self.sync_release.wait(timeout=10):
            raise TimeoutError("test did not release sync")
        emit_progress(
            progress,
            SyncPhase.FINALIZING,
            completed=3,
            total=3,
            item="EDGE-3",
            summary="Finalizing local board",
        )
        return self.report

    def reload_local(self) -> None:
        self.reload_local_calls += 1

    def reload(self) -> None:
        self.team_reload_calls += 1


class SyncProgressEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for_event(
        self,
        event: threading.Event,
        pilot: Pilot[None],
        description: str,
    ) -> None:
        for _attempt in range(150):
            if event.is_set():
                return
            await pilot.pause(0.01)
        self.fail(f"{description} did not start")

    async def _wait_for_screen(
        self,
        app: KanbanApp,
        pilot: Pilot[None],
        screen_type: type[SyncConfirmScreen] | type[SyncProgressScreen],
    ) -> SyncConfirmScreen | SyncProgressScreen:
        for _attempt in range(150):
            if isinstance(app.screen, screen_type):
                return app.screen
            await pilot.pause(0.01)
        self.fail(f"{screen_type.__name__} did not open")

    async def _wait_for_sync_idle(self, app: KanbanApp, pilot: Pilot[None]) -> None:
        for _attempt in range(200):
            if not app._sync_in_flight:
                return
            await pilot.pause(0.01)
        self.fail("sync flow did not become idle")

    async def _assert_palette_blocked(
        self,
        app: KanbanApp,
        pilot: Pilot[None],
        expected_screen: object,
        phase: str,
    ) -> None:
        stack_before = tuple(app.screen_stack)
        await pilot.press("ctrl+p")
        await pilot.pause()
        with self.subTest(phase=phase, trigger="ctrl+p"):
            self.assertIs(expected_screen, app.screen)
            self.assertEqual(stack_before, tuple(app.screen_stack))
        if isinstance(app.screen, GroupedCommandPalette):
            await app.pop_screen()
            await pilot.pause()

        stack_before = tuple(app.screen_stack)
        app.action_command_palette()  # same action used by the header Menu control
        await pilot.pause()
        with self.subTest(phase=phase, trigger="header action"):
            self.assertIs(expected_screen, app.screen)
            self.assertEqual(stack_before, tuple(app.screen_stack))
        if isinstance(app.screen, GroupedCommandPalette):
            await app.pop_screen()
            await pilot.pause()

    async def test_rapid_f5_before_preview_starts_creates_exactly_one_flow(self) -> None:
        backend = EdgeSyncBackend(block_preview=True, block_sync=True)
        app = KanbanApp(backend)

        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await asyncio.gather(*(pilot.press("f5") for _attempt in range(8)))
                await self._wait_for_event(backend.preview_started, pilot, "preview")
                await pilot.pause()

                self.assertEqual(1, backend.plan_calls)
                self.assertEqual(0, backend.sync_calls)
                self.assertEqual(
                    1,
                    sum(isinstance(screen, SyncProgressScreen) for screen in app.screen_stack),
                )

                backend.preview_release.set()
                await self._wait_for_event(backend.sync_started, pilot, "sync")
                await asyncio.gather(*(pilot.press("f5") for _attempt in range(8)))
                self.assertEqual((1, 1), (backend.plan_calls, backend.sync_calls))
        finally:
            backend.preview_release.set()
            backend.sync_release.set()
            if app.is_running:
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)

    async def test_active_sync_owns_every_priority_board_shortcut(self) -> None:
        """No app-level priority binding may mutate state behind the modal."""
        keys = ("r", "T", "n", "m", "c", "slash", "f2")
        for key in keys:
            with self.subTest(key=key):
                backend = EdgeSyncBackend(block_sync=True)
                app = KanbanApp(backend)
                try:
                    async with app.run_test(size=(100, 30)) as pilot:
                        await pilot.press("f5")
                        await self._wait_for_event(backend.sync_started, pilot, "sync")
                        progress_screen = app.screen
                        self.assertIsInstance(progress_screen, SyncProgressScreen)
                        initial_movement = app.movement_mode
                        initial_confirm = app.confirm_moves
                        initial_menu_level = app.menu_bar.level

                        await pilot.press(key)
                        await pilot.pause(0.1)

                        self.assertIs(
                            progress_screen,
                            app.screen,
                            f"{key} opened or replaced a screen during provider work",
                        )
                        self.assertEqual(0, backend.reload_local_calls, f"{key} reloaded local state")
                        self.assertEqual(0, backend.team_reload_calls, f"{key} reloaded team state")
                        self.assertFalse(backend.show_team, f"{key} changed team visibility")
                        self.assertEqual(initial_movement, app.movement_mode, f"{key} changed move mode")
                        self.assertEqual(initial_confirm, app.confirm_moves, f"{key} changed confirmation")
                        self.assertEqual(initial_menu_level, app.menu_bar.level, f"{key} changed filter bar")
                        focused_id = str(app.focused.id) if app.focused is not None else ""
                        self.assertNotEqual("bar-search", focused_id, f"{key} focused the hidden search box")
                finally:
                    backend.sync_release.set()
                    if app.is_running:
                        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)

    async def test_command_palette_is_blocked_through_every_sync_phase_then_reenabled(self) -> None:
        # Slow preview.
        preview_backend = EdgeSyncBackend(block_preview=True)
        preview_app = KanbanApp(preview_backend)
        try:
            async with preview_app.run_test(size=(100, 30)) as pilot:
                await pilot.press("f5")
                await self._wait_for_event(preview_backend.preview_started, pilot, "preview")
                await self._assert_palette_blocked(
                    preview_app,
                    pilot,
                    preview_app.screen,
                    "preview",
                )
                preview_backend.preview_release.set()
                await self._wait_for_sync_idle(preview_app, pilot)
        finally:
            preview_backend.preview_release.set()

        # Non-empty preview confirmation.
        confirm_backend = EdgeSyncBackend(
            plan=SyncPlan(creates=["Draft edge card"], create_details=[""])
        )
        confirm_app = KanbanApp(confirm_backend)
        async with confirm_app.run_test(size=(100, 30)) as pilot:
            await pilot.press("f5")
            confirm_screen = await self._wait_for_screen(confirm_app, pilot, SyncConfirmScreen)
            await pilot.pause()
            await self._assert_palette_blocked(
                confirm_app,
                pilot,
                confirm_screen,
                "confirmation",
            )
            await pilot.click("#sync-cancel")
            await self._wait_for_sync_idle(confirm_app, pilot)

        # Provider work.
        running_backend = EdgeSyncBackend(block_sync=True)
        running_app = KanbanApp(running_backend)
        try:
            async with running_app.run_test(size=(100, 30)) as pilot:
                await pilot.press("f5")
                await self._wait_for_event(running_backend.sync_started, pilot, "sync")
                await self._assert_palette_blocked(
                    running_app,
                    pilot,
                    running_app.screen,
                    "provider work",
                )
                running_backend.sync_release.set()
                await self._wait_for_sync_idle(running_app, pilot)
        finally:
            running_backend.sync_release.set()

        # Final board refresh and the completed result awaiting acknowledgement.
        final_backend = EdgeSyncBackend()
        final_app = KanbanApp(final_backend)
        refresh_started = asyncio.Event()
        refresh_release = asyncio.Event()

        async def blocked_refresh() -> None:
            refresh_started.set()
            await refresh_release.wait()

        async with final_app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            with patch.object(final_app.board, "refresh_board", new=blocked_refresh):
                await pilot.press("f5")
                await asyncio.wait_for(refresh_started.wait(), timeout=5)
                final_screen = final_app.screen
                await self._assert_palette_blocked(
                    final_app,
                    pilot,
                    final_screen,
                    "finalizing",
                )
                refresh_release.set()
                await self._wait_for_sync_idle(final_app, pilot)
                await pilot.pause()

            await self._assert_palette_blocked(
                final_app,
                pilot,
                final_screen,
                "terminal acknowledgement",
            )
            await pilot.press("escape")
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertIsInstance(final_app.screen, GroupedCommandPalette)
            await final_app.pop_screen()
            final_app.action_command_palette()
            await pilot.pause()
            self.assertIsInstance(final_app.screen, GroupedCommandPalette)

    async def test_preview_failure_is_acknowledgeable_and_a_retry_can_succeed(self) -> None:
        backend = EdgeSyncBackend(preview_failures=1)
        app = KanbanApp(backend)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("f5")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            await pilot.pause()

            failed = app.screen
            self.assertIsInstance(failed, SyncProgressScreen)
            assert isinstance(failed, SyncProgressScreen)
            self.assertIn("Failed", _label(failed, "#sync-progress-phase"))
            self.assertIn("preview endpoint unavailable", _label(failed, "#sync-progress-summary"))
            self.assertFalse(failed.query_one("#sync-progress-close", Button).disabled)
            self.assertEqual((1, 0), (backend.plan_calls, backend.sync_calls))
            self.assertFalse(app._sync_in_flight)

            await pilot.press("escape")
            await pilot.press("f5")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            await pilot.pause()

            completed = app.screen
            self.assertIsInstance(completed, SyncProgressScreen)
            assert isinstance(completed, SyncProgressScreen)
            self.assertIn("Complete", _label(completed, "#sync-progress-phase"))
            self.assertEqual((2, 1), (backend.plan_calls, backend.sync_calls))

    async def test_f5_on_local_only_board_is_a_notified_no_op_without_a_worker(self) -> None:
        class LocalOnlyBackend(JsonBackend):
            def plan_sync(self) -> SyncPlan:
                raise AssertionError("local-only F5 must not call plan_sync")

            def sync_now(self, **_options: object) -> SyncReport:
                raise AssertionError("local-only F5 must not call sync_now")

        app = KanbanApp(LocalOnlyBackend())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            root = app.screen
            worker_count = len(app.workers)

            with (
                patch.object(app, "run_worker", wraps=app.run_worker) as run_worker,
                patch.object(app, "notify", wraps=app.notify) as notify,
            ):
                await pilot.press("f5")
                await pilot.pause()

            run_worker.assert_not_called()

            self.assertIs(root, app.screen)
            self.assertEqual(1, len(app.screen_stack))
            self.assertEqual(worker_count, len(app.workers))
            self.assertFalse(app._sync_in_flight)
            notify.assert_called_once()
            self.assertIn("no provider to sync", str(notify.call_args.args[0]).lower())

    async def test_nonempty_preview_choice_matrix_preserves_send_safety(self) -> None:
        cases = (
            ("#sync-cancel", None),
            ("#sync-pull", False),
            ("#sync-send", True),
        )
        for selector, expected_push in cases:
            with self.subTest(selector=selector):
                plan = SyncPlan(creates=["Draft edge card"], create_details=[""])
                backend = EdgeSyncBackend(plan=plan)
                app = KanbanApp(backend)
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.press("f5")
                    await self._wait_for_screen(app, pilot, SyncConfirmScreen)
                    await pilot.pause()
                    self.assertEqual(0, backend.sync_calls)

                    await pilot.click(selector)
                    await self._wait_for_sync_idle(app, pilot)
                    await pilot.pause()

                    if expected_push is None:
                        self.assertEqual(0, backend.sync_calls)
                        self.assertNotIsInstance(app.screen, SyncProgressScreen)
                        continue

                    self.assertEqual(1, backend.sync_calls)
                    options = backend.sync_options[0]
                    self.assertIs(expected_push, options["push_edits"])
                    confirm = options["confirm"]
                    assert callable(confirm)
                    self.assertIs(expected_push, confirm(plan))
                    self.assertIsInstance(app.screen, SyncProgressScreen)

    async def test_hostile_preview_text_is_literal_terminal_safe_and_tiny_cancelable(self) -> None:
        app = KanbanApp(JsonBackend())
        plan = SyncPlan(
            creates=["[/]", "[bold red]OWNED[/]\x1b"],
            create_details=["", ""],
            invalid=[
                InvalidCard(
                    issue_id="draft-hostile",
                    filename="[/]\x00.md",
                    errors=("[bold red]BLOCKED[/]\x07",),
                )
            ],
        )

        async with app.run_test(size=(40, 12)) as pilot:
            root = app.screen
            screen = SyncConfirmScreen(
                "Ji[bold]ra[/]\x1b",
                "JPT[/]\x00\r\nPayments",
                plan,
            )
            await app.push_screen(screen)
            await pilot.pause()

            destination = cast(Content, screen.query_one("#sync-destination", Label).render())
            sendable = cast(Content, screen.query_one("#sync-sendable", Label).render())
            blocked = cast(Content, screen.query_one("#sync-blocked", Label).render())
            for rendered in (destination, sendable, blocked):
                # Textual converts the literal Rich Text passed to Label into
                # its equivalent unstyled Content visual at render time.
                self.assertIsInstance(rendered, Content)
                assert isinstance(rendered, Content)
                self.assertEqual([], rendered.spans)
                self.assertFalse(
                    any(
                        ord(character) < 32 and character != "\n" or ord(character) == 127
                        for character in rendered.plain
                    ),
                    repr(rendered.plain),
                )

            self.assertIn("Ji[bold]ra[/]", destination.plain)
            self.assertIn("JPT[/] Payments", destination.plain)
            self.assertIn("[/]", sendable.plain)
            self.assertIn("[bold red]OWNED[/]", sendable.plain)
            self.assertIn("[/] .md", blocked.plain)
            self.assertIn("[bold red]BLOCKED[/]", blocked.plain)
            self.assertIn("\n", sendable.plain)
            self.assertIn("\n", blocked.plain)

            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(root, app.screen)

    async def test_hostile_provider_name_is_literal_in_conflict_action_buttons(self) -> None:
        before = RemoteIssue(
            issue_id="1",
            key="JPT-1",
            title="Before",
            column_id="todo",
        )
        remote = before.model_copy(update={"title": "Provider title"})
        plan = SyncPlan(
            pushes=[
                PendingPush(
                    key="JPT-1",
                    previous=before,
                    edit=IssueEdit(title="Local title"),
                    remote=remote,
                    conflict=True,
                )
            ]
        )
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(40, 12)) as pilot:
            root = app.screen
            screen = SyncConfirmScreen("Ji[/]\x1b", "JPT", plan)
            await app.push_screen(screen)
            await pilot.pause()

            use_provider = screen.query_one("#sync-use-provider", Button)
            overwrite = screen.query_one("#sync-force", Button)
            use_provider_label = cast(Content, use_provider.label)
            overwrite_label = cast(Content, overwrite.label)
            self.assertIsInstance(use_provider_label, Content)
            self.assertIsInstance(overwrite_label, Content)
            self.assertEqual([], use_provider_label.spans)
            self.assertEqual([], overwrite_label.spans)
            self.assertIn("Ji[/]", use_provider_label.plain)
            self.assertIn("Ji[/]", overwrite_label.plain)
            self.assertFalse(
                any(
                    ord(character) < 32 or ord(character) == 127
                    for character in use_provider_label.plain + overwrite_label.plain
                )
            )

            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(root, app.screen)

    async def test_skipped_result_and_late_progress_remain_terminal_until_acknowledged(self) -> None:
        for key in ("escape", "enter"):
            with self.subTest(key=key):
                app = KanbanApp(JsonBackend())
                async with app.run_test(size=(100, 30)) as pilot:
                    root = app.screen
                    screen = SyncProgressScreen("Jira", "JPT · Payments")
                    await app.push_screen(screen)
                    screen.update_progress(
                        SyncProgressUpdate(
                            phase=SyncPhase.RECONCILING,
                            completed=4,
                            total=6,
                            item="JPT-4",
                            summary="Reconciling cards",
                        )
                    )
                    await pilot.pause()
                    screen.finish_success(SyncReport(skipped=[("JPT-5", "held locally")]))

                    # A provider callback queued after the authoritative return
                    # must not overwrite the stable terminal status.
                    await asyncio.to_thread(
                        screen.update_progress,
                        SyncProgressUpdate(
                            phase=SyncPhase.FETCHING,
                            completed=99,
                            total=100,
                            item="STALE-99",
                            summary="stale callback",
                        ),
                    )
                    await pilot.pause()

                    self.assertIn("Held", _label(screen, "#sync-progress-phase"))
                    self.assertEqual("4 / 6", _label(screen, "#sync-progress-fraction"))
                    self.assertEqual("JPT-4", _label(screen, "#sync-progress-item"))
                    self.assertIn("skipped 1", _label(screen, "#sync-progress-summary"))
                    self.assertIs(screen, app.screen)

                    await pilot.press(key)
                    await pilot.pause()
                    self.assertIs(root, app.screen)

    async def test_cross_thread_progress_burst_coalesces_to_latest_then_stays_terminal(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = SyncProgressScreen("Jira", "JPT · Payments")
            await app.push_screen(screen)
            await pilot.pause()

            def emit_burst(start: int, stop: int, *, prefix: str) -> None:
                for completed in range(start, stop + 1):
                    screen.update_progress(
                        SyncProgressUpdate(
                            phase=SyncPhase.RECONCILING,
                            completed=completed,
                            total=stop,
                            item=f"{prefix}-{completed}",
                            summary=f"{prefix} update {completed}",
                        )
                    )

            await asyncio.to_thread(emit_burst, 1, 1_000, prefix="EDGE")
            await pilot.pause()

            self.assertEqual("1000 / 1000", _label(screen, "#sync-progress-fraction"))
            self.assertEqual("EDGE-1000", _label(screen, "#sync-progress-item"))
            self.assertEqual(
                1,
                sum(isinstance(item, SyncProgressScreen) for item in app.screen_stack),
            )

            screen.finish_success(SyncReport(written=["EDGE-1000"]))
            await asyncio.to_thread(emit_burst, 1_001, 1_100, prefix="STALE")
            await pilot.pause()

            self.assertIn("Complete", _label(screen, "#sync-progress-phase"))
            self.assertEqual("1000 / 1000", _label(screen, "#sync-progress-fraction"))
            self.assertEqual("EDGE-1000", _label(screen, "#sync-progress-item"))
            self.assertIn("wrote 1", _label(screen, "#sync-progress-summary"))
            self.assertEqual(
                1,
                sum(isinstance(item, SyncProgressScreen) for item in app.screen_stack),
            )

    async def test_repeated_finish_and_close_releases_screens_and_spinner_timers(self) -> None:
        app = KanbanApp(JsonBackend())
        screen_refs: list[weakref.ReferenceType[SyncProgressScreen]] = []
        spinner_refs: list[weakref.ReferenceType[LoadingIndicator]] = []

        async with app.run_test(size=(80, 24)) as pilot:
            root = app.screen
            for index in range(12):
                screen = SyncProgressScreen("Jira", f"JPT · cycle {index}")
                await app.push_screen(screen)
                await pilot.pause()
                spinner = screen.query_one("#sync-progress-spinner", LoadingIndicator)
                screen.finish_success(SyncReport(written=[f"JPT-{index}"]))
                await pilot.pause()

                self.assertIsNone(spinner.auto_refresh)
                await pilot.click("#sync-progress-close")
                await pilot.pause()
                self.assertIs(root, app.screen)
                self.assertEqual(1, len(app.screen_stack))

                screen_refs.append(weakref.ref(screen))
                spinner_refs.append(weakref.ref(spinner))
                del screen
                del spinner

            await pilot.pause()
            gc.collect()
            await pilot.pause()
            gc.collect()

            self.assertTrue(all(reference() is None for reference in screen_refs))
            self.assertTrue(all(reference() is None for reference in spinner_refs))

    async def test_terminal_f5_is_blocked_until_the_result_is_acknowledged(self) -> None:
        backend = EdgeSyncBackend()
        app = KanbanApp(backend)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("f5")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            screen = app.screen
            self.assertIsInstance(screen, SyncProgressScreen)

            await asyncio.gather(*(pilot.press("f5") for _attempt in range(5)))
            await pilot.pause()
            self.assertIs(screen, app.screen)
            self.assertEqual((1, 1), (backend.plan_calls, backend.sync_calls))

            await pilot.press("escape")
            await pilot.press("f5")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            self.assertEqual((2, 2), (backend.plan_calls, backend.sync_calls))

    async def test_control_characters_never_reach_terminal_labels(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = SyncProgressScreen(
                "Ji\x1b[31mra\nspoofed provider\x07",
                "JPT\x00\tPayments\x7f",
            )
            await app.push_screen(screen)
            screen.update_progress(
                SyncProgressUpdate(
                    phase=SyncPhase.RECONCILING,
                    completed=1,
                    total=2,
                    item="JPT-1\x00\x1b[2J",
                    summary="Writing\x07 cards\r\nspoofed status\x7f",
                )
            )
            await pilot.pause()

            labels = (
                _label(screen, "#sync-progress-heading"),
                _label(screen, "#sync-progress-destination"),
                _label(screen, "#sync-progress-item"),
                _label(screen, "#sync-progress-summary"),
            )
            for value in labels:
                with self.subTest(value=repr(value)):
                    self.assertFalse(
                        any(ord(character) < 32 or ord(character) == 127 for character in value),
                        f"unsafe terminal control character in {value!r}",
                    )

    async def test_comments_and_verifying_phases_have_literal_user_facing_labels(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(80, 24)) as pilot:
            screen = SyncProgressScreen("Jira", "JPT")
            await app.push_screen(screen)
            for phase, expected in (
                (SyncPhase.COMMENTS, "Fetching comments"),
                (SyncPhase.VERIFYING, "Verifying removed cards"),
            ):
                with self.subTest(phase=phase):
                    screen.update_progress(SyncProgressUpdate(phase=phase))
                    await pilot.pause()
                    self.assertEqual(expected, _label(screen, "#sync-progress-phase"))

    async def test_provider_success_with_backend_reload_error_is_complete_not_retryable(self) -> None:
        report = SyncReport(written=["EDGE-1"])

        class ReloadFailBackend(EdgeSyncBackend):
            def sync_now(
                self,
                *,
                progress: Callable[[SyncProgressUpdate], None] | None = None,
                **_options: object,
            ) -> SyncReport:
                self.sync_calls += 1
                emit_progress(
                    progress,
                    SyncPhase.FINALIZING,
                    completed=3,
                    total=3,
                    item="EDGE-3",
                )
                raise PostSyncReloadError(report, OSError("workspace index unreadable"))

        backend = ReloadFailBackend()
        app = KanbanApp(backend)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("f5")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            await pilot.pause()

            screen = app.screen
            self.assertIsInstance(screen, SyncProgressScreen)
            assert isinstance(screen, SyncProgressScreen)
            self.assertIn("Complete", _label(screen, "#sync-progress-phase"))
            summary = _label(screen, "#sync-progress-summary").lower()
            self.assertIn("provider sync completed", summary)
            self.assertIn("local board reload failed", summary)
            self.assertNotIn("board refresh failed", summary)
            self.assertIn("workspace index unreadable", summary)
            self.assertIn("wrote 1", summary)
            self.assertNotIn("sync failed", summary)
            self.assertFalse(screen.query_one("#sync-progress-close", Button).disabled)

    async def test_tiny_live_resize_all_themes_and_animation_none_keep_close_reachable(self) -> None:
        app = KanbanApp(JsonBackend())
        app.animation_level = "none"

        async with app.run_test(size=(100, 30)) as pilot:
            screen = SyncProgressScreen("Jira", "JPT · Payments")
            await app.push_screen(screen)
            screen.update_progress(
                SyncProgressUpdate(
                    phase=SyncPhase.RECONCILING,
                    completed=6,
                    total=12,
                    item="JPT-12345",
                    summary="Writing cards to Markdown",
                )
            )
            await pilot.resize_terminal(40, 12)
            await pilot.pause()

            for theme in sorted(app.available_themes):
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.pause()
                    dialog = screen.query_one("#sync-progress-dialog", Vertical)
                    content = screen.query_one("#sync-progress-content", VerticalScroll)
                    close = screen.query_one("#sync-progress-close", Button)
                    spinner = screen.query_one("#sync-progress-spinner", LoadingIndicator)
                    phase = screen.query_one("#sync-progress-phase", Label)
                    fraction = screen.query_one("#sync-progress-fraction", Label)
                    self.assertLessEqual(dialog.region.right, 40)
                    self.assertLessEqual(dialog.region.bottom, 12)
                    self.assertGreater(close.region.width, 0)
                    self.assertLessEqual(close.region.bottom, dialog.content_region.bottom)
                    self.assertGreaterEqual(phase.region.y, content.content_region.y)
                    self.assertLessEqual(fraction.region.bottom, content.content_region.bottom)
                    self.assertEqual("Reconciling Markdown", str(phase.render()))
                    self.assertEqual("6 / 12", str(fraction.render()))
                    self.assertEqual("Loading...", str(spinner.render()))

            screen.finish_success(SyncReport(written=["JPT-1"]))
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            close = screen.query_one("#sync-progress-close", Button)
            self.assertFalse(close.disabled)
            self.assertGreater(close.region.width, 0)
            self.assertLessEqual(close.region.bottom, 24)
            self.assertEqual("6 / 12", _label(screen, "#sync-progress-fraction"))

    async def test_close_stays_locked_until_post_sync_board_refresh_returns(self) -> None:
        backend = EdgeSyncBackend()
        app = KanbanApp(backend)
        refresh_started = asyncio.Event()
        refresh_release = asyncio.Event()

        async def blocked_refresh() -> None:
            refresh_started.set()
            await refresh_release.wait()

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            widget_refresh_close_states: list[bool] = []
            view_refresh_close_states: list[bool] = []
            work_items = app.query_one(WorkItemsView)

            def refresh_work_items() -> None:
                current = app.screen
                assert isinstance(current, SyncProgressScreen)
                widget_refresh_close_states.append(
                    current.query_one("#sync-progress-close", Button).disabled
                )

            def refresh_view() -> None:
                current = app.screen
                assert isinstance(current, SyncProgressScreen)
                view_refresh_close_states.append(
                    current.query_one("#sync-progress-close", Button).disabled
                )

            with (
                patch.object(app.board, "refresh_board", new=blocked_refresh),
                patch.object(work_items, "refresh_tasks", new=refresh_work_items),
                patch.object(app, "refresh_view", new=refresh_view),
            ):
                await pilot.press("f5")
                await asyncio.wait_for(refresh_started.wait(), timeout=5)
                await pilot.pause()
                screen = app.screen
                self.assertIsInstance(screen, SyncProgressScreen)
                assert isinstance(screen, SyncProgressScreen)
                close = screen.query_one("#sync-progress-close", Button)

                try:
                    self.assertTrue(
                        close.disabled,
                        "Close must stay locked until post-sync board refresh finishes",
                    )
                    await pilot.press("escape")
                    await pilot.press("ctrl+q")
                    await app.action_quit()  # header quit uses this same action
                    await pilot.pause()
                    self.assertIs(screen, app.screen)
                    self.assertTrue(app.is_running)
                finally:
                    refresh_release.set()
                    await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)

                self.assertFalse(close.disabled)
                self.assertFalse(app._sync_in_flight)
                self.assertEqual([True], widget_refresh_close_states)
                self.assertEqual([True], view_refresh_close_states)

    async def test_board_refresh_failure_is_reported_without_hiding_provider_success(self) -> None:
        backend = EdgeSyncBackend(report=SyncReport(written=["EDGE-1"]))
        app = KanbanApp(backend)

        async def failed_refresh() -> None:
            raise OSError("local board became unreadable")

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            with patch.object(app.board, "refresh_board", new=failed_refresh):
                await pilot.press("f5")
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                await pilot.pause()

            screen = app.screen
            self.assertIsInstance(screen, SyncProgressScreen)
            assert isinstance(screen, SyncProgressScreen)
            summary = _label(screen, "#sync-progress-summary").lower()
            self.assertIn("complete", _label(screen, "#sync-progress-phase").lower())
            self.assertIn("wrote 1", summary)
            self.assertIn("board refresh failed", summary)
            self.assertNotIn("local board reload failed", summary)
            self.assertIn("local board became unreadable", summary)
            self.assertFalse(screen.query_one("#sync-progress-close", Button).disabled)
            self.assertFalse(app._sync_in_flight)


if __name__ == "__main__":
    unittest.main()
