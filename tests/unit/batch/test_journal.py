"""Phase-aware batch apply state survives process failure."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pykantui.batch.journal import BatchApplyJournal, BatchApplyPhase
from pykantui.tracker.models import RemoteIssue


class JournalTests(unittest.TestCase):
    def test_confirmed_create_keeps_the_remote_issue_before_transition(self) -> None:
        issue = RemoteIssue(issue_id="10001", key="PAY-1", title="Parent", column_id="1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch.json"
            journal = BatchApplyJournal(batch_id="checkout", plan_hash="abc")
            journal.begin_create(path, "parent", signature="sig")
            journal.confirm_create(path, "parent", issue)
            loaded = BatchApplyJournal.load(path, batch_id="checkout", plan_hash="abc")

        record = loaded.items["parent"]
        self.assertEqual(record.phase, BatchApplyPhase.CREATED)
        self.assertEqual(record.remote_issue.key if record.remote_issue else "", "PAY-1")

    def test_transition_progress_is_durable_per_hop(self) -> None:
        issue = RemoteIssue(issue_id="10001", key="PAY-1", title="Parent", column_id="1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch.json"
            journal = BatchApplyJournal(batch_id="checkout", plan_hash="abc")
            journal.begin_create(path, "parent", signature="sig")
            journal.confirm_create(path, "parent", issue)
            journal.begin_transition(path, "parent", hop=0, column_id="2")
            journal.confirm_transition(
                path,
                "parent",
                hop=0,
                issue=issue.model_copy(update={"column_id": "2"}),
                complete=False,
            )
            loaded = BatchApplyJournal.load(path, batch_id="checkout", plan_hash="abc")

        self.assertEqual(loaded.items["parent"].phase, BatchApplyPhase.CREATED)
        self.assertEqual(loaded.items["parent"].next_transition, 1)


if __name__ == "__main__":
    unittest.main()
