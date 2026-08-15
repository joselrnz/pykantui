"""Provider-neutral values passed from synchronous Sync work to its callers."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate, emit_progress


class SyncProgressValueTests(unittest.TestCase):
    def test_every_user_visible_sync_phase_has_a_stable_provider_neutral_value(self) -> None:
        self.assertEqual(
            {
                "preparing",
                "applying",
                "fetching",
                "comments",
                "reconciling",
                "verifying",
                "finalizing",
                "complete",
                "held",
                "failed",
            },
            {phase.value for phase in SyncPhase},
        )

    def test_progress_update_carries_fraction_current_item_and_terminal_state(self) -> None:
        update = SyncProgressUpdate(
            phase=SyncPhase.RECONCILING,
            completed=3,
            total=5,
            item="JPT-4",
            summary="Writing provider cards to Markdown",
            active=True,
            error=False,
        )

        self.assertEqual((3, 5), (update.completed, update.total))
        self.assertEqual("JPT-4", update.item)
        self.assertTrue(update.active)
        self.assertFalse(update.error)

    def test_progress_updates_are_immutable_snapshots_safe_to_cross_threads(self) -> None:
        update = SyncProgressUpdate(phase=SyncPhase.PREPARING)

        with self.assertRaises(FrozenInstanceError):
            update.__setattr__("completed", 1)

    def test_emit_progress_delivers_one_exact_snapshot(self) -> None:
        seen: list[SyncProgressUpdate] = []

        emit_progress(
            seen.append,
            SyncPhase.APPLYING,
            completed=1,
            total=6,
            item="JPT-2",
            summary="Sending approved changes",
        )

        self.assertEqual(1, len(seen))
        self.assertEqual(SyncPhase.APPLYING, seen[0].phase)
        self.assertEqual((1, 6, "JPT-2"), (seen[0].completed, seen[0].total, seen[0].item))

    def test_emit_progress_is_an_inert_noop_when_the_caller_does_not_subscribe(self) -> None:
        emit_progress(
            None,
            SyncPhase.FETCHING,
            completed=12,
            total=None,
            item="JPT-12",
        )

    def test_a_broken_observer_never_changes_sync_control_flow(self) -> None:
        def broken_observer(_update: SyncProgressUpdate) -> None:
            raise RuntimeError("UI was already closed")

        update = emit_progress(
            broken_observer,
            SyncPhase.APPLYING,
            completed=2,
            total=5,
            item="JPT-2",
        )

        self.assertEqual(
            (SyncPhase.APPLYING, 2, 5, "JPT-2"),
            (
                update.phase,
                update.completed,
                update.total,
                update.item,
            ),
        )


if __name__ == "__main__":
    unittest.main()
