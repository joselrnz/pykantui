"""Low-level progress contracts at malformed, terminal, and large boundaries."""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue
from typing import cast

from pykantui.workspace.models import SyncReport
from pykantui.workspace.progress import (
    ProgressCounter,
    SyncPhase,
    SyncProgressCallback,
    SyncProgressUpdate,
    collect_items,
    emit_progress,
    report_sync_progress,
    tracked_items,
)


class ProgressInputBoundaryTests(unittest.TestCase):
    def test_provider_text_cannot_carry_terminal_control_characters_to_any_observer(self) -> None:
        update = emit_progress(
            None,
            SyncPhase.FETCHING,
            item="JPT-1\x00\x1b[2J\x7f",
            summary="Reading\x07 cards\r\nspoofed status",
        )

        for value in (update.item, update.summary):
            with self.subTest(value=repr(value)):
                self.assertFalse(
                    any(ord(character) < 32 or ord(character) == 127 for character in value),
                    value,
                )

    def test_malformed_runtime_counts_are_sanitized_instead_of_breaking_sync(self) -> None:
        malformed: tuple[object, ...] = ("3", 1.5, True, object())

        for value in malformed:
            with self.subTest(value=repr(value)):
                update = emit_progress(
                    None,
                    SyncPhase.APPLYING,
                    completed=cast(int, value),
                    total=cast(int, value),
                )

                self.assertEqual(0, update.completed)
                self.assertEqual(0, update.total)

    def test_a_known_fraction_never_reports_more_completed_than_total(self) -> None:
        update = emit_progress(
            None,
            SyncPhase.RECONCILING,
            completed=9,
            total=3,
        )

        self.assertEqual((3, 3), (update.completed, update.total))

    def test_negative_values_collapse_to_a_valid_zero_fraction(self) -> None:
        update = emit_progress(
            None,
            SyncPhase.APPLYING,
            completed=-9,
            total=-3,
        )

        self.assertEqual((0, 0), (update.completed, update.total))

    def test_unknown_total_keeps_an_unbounded_non_negative_count(self) -> None:
        update = emit_progress(
            None,
            SyncPhase.FETCHING,
            completed=9,
            total=None,
        )

        self.assertEqual((9, None), (update.completed, update.total))


class ProgressCollectionBoundaryTests(unittest.TestCase):
    def test_unknown_length_collection_reports_zero_then_every_one_of_1000_items(self) -> None:
        seen: list[SyncProgressUpdate] = []

        found = collect_items(
            (number for number in range(1000)),
            seen.append,
            SyncPhase.FETCHING,
            str,
            "Downloading provider cards",
        )

        self.assertEqual(list(range(1000)), found)
        self.assertEqual(1001, len(seen))
        self.assertEqual(list(range(1001)), [update.completed for update in seen])
        self.assertTrue(all(update.total is None for update in seen))
        self.assertEqual("999", seen[-1].item)

    def test_large_determinate_counter_is_monotonic_and_finishes_exactly(self) -> None:
        seen: list[SyncProgressUpdate] = []
        counter = ProgressCounter(
            seen.append,
            SyncPhase.RECONCILING,
            1000,
            "Writing provider cards to Markdown",
            announce_each=False,
        )

        handled = list(tracked_items(range(1000), counter, str))

        self.assertEqual(list(range(1000)), handled)
        self.assertEqual(1001, len(seen))
        self.assertEqual(list(range(1001)), [update.completed for update in seen])
        self.assertTrue(all(update.total == 1000 for update in seen))
        self.assertEqual("999", seen[-1].item)

    def test_a_loop_failure_does_not_claim_the_current_item_finished(self) -> None:
        seen: list[SyncProgressUpdate] = []
        counter = ProgressCounter(seen.append, SyncPhase.APPLYING, 3, "Sending")

        with self.assertRaisesRegex(RuntimeError, "second write failed"):
            for number in tracked_items(range(3), counter, str):
                if number == 1:
                    raise RuntimeError("second write failed")

        self.assertEqual(1, counter.completed)
        self.assertEqual((1, "1"), (seen[-1].completed, seen[-1].item))


class TerminalProgressContractTests(unittest.TestCase):
    def test_success_emits_exactly_one_inactive_terminal_snapshot(self) -> None:
        seen: list[SyncProgressUpdate] = []

        @report_sync_progress
        def operation(*, progress: SyncProgressCallback | None = None) -> SyncReport:
            emit_progress(
                progress,
                SyncPhase.FINALIZING,
                completed=4,
                total=4,
                item="K-4",
            )
            return SyncReport(written=["K-1.md"])

        operation(progress=seen.append)

        terminal = [update for update in seen if not update.active]
        self.assertEqual(1, len(terminal))
        self.assertEqual(SyncPhase.COMPLETE, terminal[0].phase)
        self.assertEqual((4, 4, "K-4"), (terminal[0].completed, terminal[0].total, terminal[0].item))

    def test_held_report_emits_exactly_one_non_error_terminal_snapshot(self) -> None:
        seen: list[SyncProgressUpdate] = []

        @report_sync_progress
        def operation(*, progress: SyncProgressCallback | None = None) -> SyncReport:
            emit_progress(
                progress,
                SyncPhase.APPLYING,
                completed=1,
                total=1,
                item="K-1",
            )
            return SyncReport(held=["K-1.md"])

        operation(progress=seen.append)

        terminal = [update for update in seen if not update.active]
        self.assertEqual(1, len(terminal))
        self.assertEqual(SyncPhase.HELD, terminal[0].phase)
        self.assertFalse(terminal[0].error)

    def test_failure_emits_exactly_one_error_terminal_then_reraises(self) -> None:
        seen: list[SyncProgressUpdate] = []

        @report_sync_progress
        def operation(*, progress: SyncProgressCallback | None = None) -> SyncReport:
            emit_progress(
                progress,
                SyncPhase.FETCHING,
                completed=2,
                total=None,
                item="K-2",
            )
            raise RuntimeError("provider page failed\nsecret detail")

        with self.assertRaisesRegex(RuntimeError, "provider page failed"):
            operation(progress=seen.append)

        terminal = [update for update in seen if not update.active]
        self.assertEqual(1, len(terminal))
        self.assertEqual(SyncPhase.FAILED, terminal[0].phase)
        self.assertTrue(terminal[0].error)
        self.assertEqual((2, None, "K-2"), (terminal[0].completed, terminal[0].total, terminal[0].item))
        self.assertEqual("provider page failed", terminal[0].summary)

    def test_observer_exceptions_cannot_hide_or_duplicate_the_terminal_state(self) -> None:
        delivered: list[SyncProgressUpdate] = []

        def broken_observer(update: SyncProgressUpdate) -> None:
            delivered.append(update)
            raise RuntimeError("dialog unmounted")

        @report_sync_progress
        def operation(*, progress: SyncProgressCallback | None = None) -> SyncReport:
            emit_progress(
                progress,
                SyncPhase.FINALIZING,
                completed=1,
                total=1,
            )
            return SyncReport()

        report = operation(progress=broken_observer)

        self.assertEqual("no changes", report.summary())
        self.assertEqual(1, len([update for update in delivered if not update.active]))

    def test_snapshots_can_be_delivered_from_many_worker_threads_without_shared_state(self) -> None:
        delivered: SimpleQueue[SyncProgressUpdate] = SimpleQueue()

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [
                pool.submit(
                    emit_progress,
                    delivered.put,
                    SyncPhase.FETCHING,
                    completed=index,
                    total=None,
                    item=f"K-{index}",
                )
                for index in range(200)
            ]
            snapshots = [future.result() for future in futures]

        observed = [delivered.get_nowait() for _ in range(200)]
        self.assertEqual(set(range(200)), {update.completed for update in snapshots})
        self.assertEqual(set(range(200)), {update.completed for update in observed})
        self.assertEqual(200, len({id(update) for update in snapshots}))


if __name__ == "__main__":
    unittest.main()
