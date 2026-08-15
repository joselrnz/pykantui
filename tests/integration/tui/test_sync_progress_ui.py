"""Sync remains observable from first request through explicit acknowledgement."""

from __future__ import annotations

import asyncio
import threading
import unittest
from collections.abc import Callable

from textual.containers import Vertical
from textual.pilot import Pilot
from textual.widgets import Button, Label, LoadingIndicator

from pykantui.pages.sync import SyncProgressScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.workspace.models import SyncPlan, SyncReport
from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate, emit_progress


def _label(screen: SyncProgressScreen, selector: str) -> str:
    return str(screen.query_one(selector, Label).render())


class ControlledSyncBackend(JsonBackend):
    """A provider-shaped backend whose worker can be inspected while active."""

    supports_sync = True

    def __init__(
        self,
        *,
        fail: bool = False,
        held: bool = False,
        block_preview: bool = False,
        block_after_terminal: bool = False,
    ) -> None:
        super().__init__()
        self.fail = fail
        self.held = held
        self.block_preview = block_preview
        self.block_after_terminal = block_after_terminal
        self.plan_calls = 0
        self.sync_calls = 0
        self.preview_started = threading.Event()
        self.preview_release = threading.Event()
        self.started = threading.Event()
        self.release = threading.Event()
        self.terminal_emitted = threading.Event()
        self.return_release = threading.Event()

    def display_kind(self) -> str:
        return "Recorder"

    def plan_sync(self) -> SyncPlan:
        self.plan_calls += 1
        self.preview_started.set()
        if self.block_preview and not self.preview_release.wait(timeout=10):
            raise TimeoutError("test did not release the controlled Sync preview")
        return SyncPlan()

    def sync_now(
        self,
        *,
        progress: Callable[[SyncProgressUpdate], None] | None = None,
        **_options: object,
    ) -> SyncReport:
        self.sync_calls += 1
        emit_progress(progress, SyncPhase.PREPARING, summary="Checking local Markdown")
        emit_progress(
            progress,
            SyncPhase.RECONCILING,
            completed=3,
            total=5,
            item="JPT-4",
            summary="Writing cards to Markdown",
        )
        self.started.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test did not release the controlled Sync worker")
        if self.fail:
            raise RuntimeError("provider refused the request")
        emit_progress(progress, SyncPhase.FINALIZING, completed=5, total=5)
        if self.block_after_terminal:
            emit_progress(
                progress,
                SyncPhase.COMPLETE,
                completed=5,
                total=5,
                summary="wrote 1",
                active=False,
            )
            self.terminal_emitted.set()
            if not self.return_release.wait(timeout=10):
                raise TimeoutError("test did not release the backend after terminal progress")
        if self.held:
            return SyncReport(written=["JPT-1"], held=["JPT-2.md"])
        return SyncReport(written=["JPT-1"])


class SyncProgressScreenTests(unittest.IsolatedAsyncioTestCase):
    async def _open(self, app: KanbanApp) -> SyncProgressScreen:
        screen = SyncProgressScreen("Jira", "JPT · Payments")
        await app.push_screen(screen)
        return screen

    async def test_active_screen_has_loading_phase_fraction_item_and_disabled_close(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = await self._open(app)
            await pilot.pause()
            spinner = screen.query_one("#sync-progress-spinner", LoadingIndicator)
            close = screen.query_one("#sync-progress-close", Button)

            self.assertIs(screen, app.screen)
            self.assertIsNotNone(spinner.auto_refresh)
            self.assertTrue(spinner.display)
            self.assertTrue(close.disabled)
            self.assertTrue(_label(screen, "#sync-progress-heading"))
            visible_text = " ".join(str(label.render()) for label in screen.query(Label))
            self.assertIn("Jira", visible_text)
            self.assertIn("JPT · Payments", visible_text)
            self.assertIn("Preparing", _label(screen, "#sync-progress-phase"))
            self.assertTrue(_label(screen, "#sync-progress-fraction"))
            self.assertEqual("—", _label(screen, "#sync-progress-item"))
            self.assertTrue(_label(screen, "#sync-progress-summary"))

    async def test_worker_thread_callback_updates_the_real_fraction_and_current_card(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = await self._open(app)
            await pilot.pause()

            await asyncio.to_thread(
                screen.update_progress,
                SyncProgressUpdate(
                    phase=SyncPhase.RECONCILING,
                    completed=3,
                    total=5,
                    item="JPT-4",
                    summary="Writing cards to Markdown",
                ),
            )
            await pilot.pause()

            self.assertIn("Reconciling", _label(screen, "#sync-progress-phase"))
            self.assertEqual("3 / 5", _label(screen, "#sync-progress-fraction"))
            self.assertEqual("JPT-4", _label(screen, "#sync-progress-item"))
            self.assertEqual("Writing cards to Markdown", _label(screen, "#sync-progress-summary"))

    async def test_provider_supplied_item_text_is_literal_not_rich_markup(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = SyncProgressScreen("[bold red]Jira[/]", "[link]Payments[/link]")
            await app.push_screen(screen)
            await pilot.pause()
            screen.update_progress(
                SyncProgressUpdate(
                    phase=SyncPhase.RECONCILING,
                    completed=1,
                    total=2,
                    item="[bold red]JPT-4[/]",
                    summary="[link]Writing Markdown[/link]",
                )
            )
            await pilot.pause()

            visible_text = " ".join(str(label.render()) for label in screen.query(Label))
            self.assertIn("[bold red]Jira[/]", visible_text)
            self.assertIn("[link]Payments[/link]", visible_text)
            self.assertEqual("[bold red]JPT-4[/]", _label(screen, "#sync-progress-item"))
            self.assertEqual("[link]Writing Markdown[/link]", _label(screen, "#sync-progress-summary"))

    async def test_success_stops_animation_and_persists_until_close_is_clicked(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            root = app.screen
            screen = await self._open(app)
            await pilot.pause()
            screen.update_progress(
                SyncProgressUpdate(
                    phase=SyncPhase.FINALIZING,
                    completed=5,
                    total=5,
                    item="JPT-5",
                )
            )
            screen.finish_success(SyncReport(written=["JPT-1", "JPT-2"]))
            await pilot.pause()

            spinner = screen.query_one("#sync-progress-spinner", LoadingIndicator)
            close = screen.query_one("#sync-progress-close", Button)
            self.assertIs(screen, app.screen)
            self.assertFalse(spinner.display)
            self.assertIsNone(spinner.auto_refresh)
            self.assertFalse(close.disabled)
            self.assertIs(close, app.focused)
            self.assertIn("Complete", _label(screen, "#sync-progress-phase"))
            self.assertEqual("5 / 5", _label(screen, "#sync-progress-fraction"))
            self.assertIn("wrote 2", _label(screen, "#sync-progress-summary"))

            await pilot.pause(0.15)
            self.assertIs(screen, app.screen)
            await pilot.click("#sync-progress-close")
            await pilot.pause()
            self.assertIs(root, app.screen)

    async def test_held_and_failed_results_are_named_not_communicated_by_color_alone(self) -> None:
        for outcome in ("held", "failed"):
            with self.subTest(outcome=outcome):
                app = KanbanApp(JsonBackend())
                async with app.run_test(size=(100, 30)) as pilot:
                    screen = await self._open(app)
                    await pilot.pause()
                    if outcome == "held":
                        screen.finish_success(SyncReport(written=["JPT-1"], held=["JPT-2.md"]))
                    else:
                        screen.finish_error("provider refused the request")
                    await pilot.pause()

                    phase = _label(screen, "#sync-progress-phase")
                    summary = _label(screen, "#sync-progress-summary")
                    self.assertIn(outcome.title(), phase)
                    self.assertFalse(screen.query_one("#sync-progress-close", Button).disabled)
                    self.assertIsNone(screen.query_one("#sync-progress-spinner", LoadingIndicator).auto_refresh)
                    if outcome == "held":
                        self.assertIn("held 1", summary)
                    else:
                        self.assertEqual("provider refused the request", summary)
                    await pilot.pause(0.1)
                    self.assertIs(screen, app.screen)

    async def test_failure_preserves_the_last_partial_fetch_count(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = await self._open(app)
            screen.update_progress(
                SyncProgressUpdate(
                    phase=SyncPhase.FETCHING,
                    completed=1,
                    total=None,
                    item="JPT-1",
                    summary="Fetching cards",
                )
            )
            await pilot.pause()
            screen.finish_error("provider page failed")
            await pilot.pause()

            self.assertIn("Failed", _label(screen, "#sync-progress-phase"))
            self.assertEqual("1 card fetched", _label(screen, "#sync-progress-fraction"))
            self.assertEqual("JPT-1", _label(screen, "#sync-progress-item"))
            self.assertEqual("provider page failed", _label(screen, "#sync-progress-summary"))

    async def test_zero_card_terminal_result_says_zero_cards_instead_of_zero_over_zero(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = await self._open(app)
            screen.update_progress(
                SyncProgressUpdate(
                    phase=SyncPhase.RECONCILING,
                    completed=0,
                    total=0,
                    summary="No provider cards returned",
                )
            )
            await pilot.pause()
            screen.finish_success(SyncReport())
            await pilot.pause()

            self.assertIn("Complete", _label(screen, "#sync-progress-phase"))
            self.assertEqual("0 cards", _label(screen, "#sync-progress-fraction"))
            self.assertNotEqual("0 / 0", _label(screen, "#sync-progress-fraction"))

    async def test_escape_enter_and_f5_cannot_dismiss_or_duplicate_an_active_sync(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(100, 30)) as pilot:
            screen = await self._open(app)
            await pilot.pause()

            await pilot.press("escape")
            await pilot.press("enter")
            await pilot.press("f5")
            await pilot.pause()

            self.assertIs(screen, app.screen)
            self.assertEqual(1, sum(isinstance(item, SyncProgressScreen) for item in app.screen_stack))
            self.assertTrue(screen.query_one("#sync-progress-close", Button).disabled)

    async def test_dialog_fits_eighty_by_twenty_four_and_remains_legible_in_every_theme(self) -> None:
        app = KanbanApp(JsonBackend())

        async with app.run_test(size=(80, 24)) as pilot:
            screen = await self._open(app)
            screen.update_progress(
                SyncProgressUpdate(
                    phase=SyncPhase.RECONCILING,
                    completed=6,
                    total=12,
                    item="JPT-12345",
                    summary="Writing cards to Markdown",
                )
            )
            await pilot.pause()

            for theme in sorted(app.available_themes):
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.pause()
                    dialog = screen.query_one("#sync-progress-dialog", Vertical)
                    close = screen.query_one("#sync-progress-close", Button)
                    self.assertLessEqual(dialog.region.right, app.screen.region.right)
                    self.assertLessEqual(dialog.region.bottom, app.screen.region.bottom)
                    self.assertLessEqual(close.region.bottom, dialog.content_region.bottom)
                    self.assertEqual("6 / 12", _label(screen, "#sync-progress-fraction"))
                    self.assertEqual("JPT-12345", _label(screen, "#sync-progress-item"))

    async def test_animation_level_none_uses_textual_static_loading_fallback(self) -> None:
        app = KanbanApp(JsonBackend())
        app.animation_level = "none"

        async with app.run_test(size=(80, 24)) as pilot:
            screen = await self._open(app)
            await pilot.pause()
            spinner = screen.query_one("#sync-progress-spinner", LoadingIndicator)

            self.assertEqual("Loading...", str(spinner.render()))
            self.assertIn("Preparing", _label(screen, "#sync-progress-phase"))


class SyncProgressControllerTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for_event(
        self,
        event: threading.Event,
        pilot: Pilot[None],
        description: str,
    ) -> None:
        for _attempt in range(100):
            if event.is_set():
                return
            await pilot.pause(0.01)
        self.fail(f"{description} did not start")

    async def _wait_until_started(self, backend: ControlledSyncBackend, pilot: Pilot[None]) -> None:
        await self._wait_for_event(backend.started, pilot, "Sync worker")

    async def _wait_until_terminal(
        self,
        screen: SyncProgressScreen,
        pilot: Pilot[None],
    ) -> None:
        close = screen.query_one("#sync-progress-close", Button)
        for _attempt in range(100):
            if not close.disabled:
                return
            await pilot.pause(0.01)
        self.fail("Sync result did not become acknowledgeable")

    async def test_progress_dialog_is_visible_while_the_preview_is_still_running(self) -> None:
        backend = ControlledSyncBackend(block_preview=True)
        app = KanbanApp(backend)

        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("f5")
                await self._wait_for_event(backend.preview_started, pilot, "Sync preview")
                await pilot.pause()

                screen = app.screen
                self.assertIsInstance(screen, SyncProgressScreen)
                assert isinstance(screen, SyncProgressScreen)
                self.assertIn("Preparing", _label(screen, "#sync-progress-phase"))
                self.assertTrue(screen.query_one("#sync-progress-close", Button).disabled)
                self.assertEqual((1, 0), (backend.plan_calls, backend.sync_calls))

                backend.preview_release.set()
                await self._wait_until_started(backend, pilot)
                backend.release.set()
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
        finally:
            backend.preview_release.set()
            backend.release.set()

    async def test_f5_shows_live_worker_updates_and_terminal_result_until_explicit_close(self) -> None:
        backend = ControlledSyncBackend()
        app = KanbanApp(backend)

        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("f5")
                await self._wait_until_started(backend, pilot)
                await pilot.pause()

                screen = app.screen
                self.assertIsInstance(screen, SyncProgressScreen)
                assert isinstance(screen, SyncProgressScreen)
                self.assertEqual("3 / 5", _label(screen, "#sync-progress-fraction"))
                self.assertEqual("JPT-4", _label(screen, "#sync-progress-item"))

                await pilot.press("f5")
                await pilot.press("escape")
                await pilot.pause()
                self.assertEqual((1, 1), (backend.plan_calls, backend.sync_calls))
                self.assertIs(screen, app.screen)

                backend.release.set()
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                await self._wait_until_terminal(screen, pilot)

                self.assertIs(screen, app.screen)
                self.assertIn("Complete", _label(screen, "#sync-progress-phase"))
                self.assertIn("wrote 1", _label(screen, "#sync-progress-summary"))
                self.assertFalse(screen.query_one("#sync-progress-close", Button).disabled)
                await pilot.click("#sync-progress-close")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, SyncProgressScreen)
        finally:
            backend.release.set()

    async def test_no_change_sync_can_finish_before_progress_children_mount(self) -> None:
        """A fast provider result must wait for the modal's composed controls."""
        backend = ControlledSyncBackend()
        backend.release.set()
        app = KanbanApp(backend)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("f5")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            await pilot.pause()

            screen = app.screen
            self.assertIsInstance(screen, SyncProgressScreen)
            assert isinstance(screen, SyncProgressScreen)
            self.assertIn("Complete", _label(screen, "#sync-progress-phase"))
            self.assertFalse(screen.query_one("#sync-progress-close", Button).disabled)
            self.assertEqual((1, 1), (backend.plan_calls, backend.sync_calls))

    async def test_keyboard_and_header_exit_cannot_kill_an_active_provider_write(self) -> None:
        backend = ControlledSyncBackend()
        app = KanbanApp(backend)

        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("f5")
                await self._wait_until_started(backend, pilot)
                screen = app.screen
                self.assertIsInstance(screen, SyncProgressScreen)
                assert isinstance(screen, SyncProgressScreen)

                await pilot.press("ctrl+q")
                await app.action_quit()  # same action used by the header × click
                await pilot.pause()

                self.assertTrue(app.is_running)
                self.assertIs(screen, app.screen)
                self.assertEqual((1, 1), (backend.plan_calls, backend.sync_calls))
                self.assertFalse(backend.release.is_set())

                backend.release.set()
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                await self._wait_until_terminal(screen, pilot)

                self.assertTrue(app.is_running)
                self.assertIs(screen, app.screen)
                self.assertIn("Complete", _label(screen, "#sync-progress-phase"))
        finally:
            backend.release.set()

    async def test_worker_can_finish_after_its_progress_screen_is_unmounted(self) -> None:
        backend = ControlledSyncBackend()
        app = KanbanApp(backend)

        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("f5")
                await self._wait_until_started(backend, pilot)
                progress_screen = app.screen
                self.assertIsInstance(progress_screen, SyncProgressScreen)

                # Reproduce a concurrent screen-stack change while the provider
                # thread is still working. Its successful return must not query
                # widgets which Textual has already removed from the screen.
                await app.pop_screen()
                await pilot.pause()
                self.assertNotIn(progress_screen, app.screen_stack)

                backend.release.set()
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                await pilot.pause()

                self.assertEqual((1, 1), (backend.plan_calls, backend.sync_calls))
                self.assertFalse(app._sync_in_flight)
        finally:
            backend.release.set()

    async def test_terminal_callback_cannot_dismiss_dialog_before_worker_returns(self) -> None:
        backend = ControlledSyncBackend(block_after_terminal=True)
        app = KanbanApp(backend)

        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("f5")
                await self._wait_until_started(backend, pilot)
                progress_screen = app.screen
                self.assertIsInstance(progress_screen, SyncProgressScreen)
                assert isinstance(progress_screen, SyncProgressScreen)

                backend.release.set()
                await self._wait_for_event(
                    backend.terminal_emitted,
                    pilot,
                    "Terminal progress callback",
                )
                # Give Textual enough event-loop turns to consume the
                # cross-thread terminal message while sync_now remains blocked.
                await pilot.pause(0.15)
                close = progress_screen.query_one("#sync-progress-close", Button)

                try:
                    self.assertTrue(
                        close.disabled,
                        "terminal progress must not enable Close before sync_now returns",
                    )
                    await pilot.press("escape")
                    await pilot.pause()
                    self.assertIs(progress_screen, app.screen)
                finally:
                    backend.return_release.set()
                    await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)

                await self._wait_until_terminal(progress_screen, pilot)
                self.assertFalse(close.disabled)
        finally:
            backend.release.set()
            backend.return_release.set()

    async def test_worker_error_stays_in_the_dialog_instead_of_disappearing_into_a_toast(self) -> None:
        backend = ControlledSyncBackend(fail=True)
        app = KanbanApp(backend)

        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("f5")
                await self._wait_until_started(backend, pilot)
                backend.release.set()
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                screen = app.screen
                self.assertIsInstance(screen, SyncProgressScreen)
                assert isinstance(screen, SyncProgressScreen)
                await self._wait_until_terminal(screen, pilot)

                self.assertIn("Failed", _label(screen, "#sync-progress-phase"))
                self.assertIn("provider refused", _label(screen, "#sync-progress-summary"))
                self.assertFalse(screen.query_one("#sync-progress-close", Button).disabled)
                await pilot.pause(0.15)
                self.assertIs(screen, app.screen)
        finally:
            backend.release.set()


if __name__ == "__main__":
    unittest.main()
