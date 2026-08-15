"""Domain model behaviour."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from pykantui.core.workflows import DONE_COLUMN, IN_PROGRESS_COLUMN, TODO_COLUMN, WAITING_COLUMN
from pykantui.models import Board, MoveResult, Task
from pykantui.sync.jsonstore import JsonBackend


class ColumnTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.board = Board(
            board_id=1,
            name="B",
            reset_column=TODO_COLUMN,
            start_column=IN_PROGRESS_COLUMN,
            finish_column=DONE_COLUMN,
        )

    def test_start_column_stamps_started_at(self) -> None:
        task = Task(task_id=1, title="t", column_id=1)
        task.apply_column_transition(IN_PROGRESS_COLUMN, self.board)

        self.assertEqual(task.column_id, 2)
        self.assertIsNotNone(task.started_at)
        self.assertIsNone(task.finished_at)

    def test_finish_column_stamps_both(self) -> None:
        task = Task(task_id=1, title="t", column_id=1)
        task.apply_column_transition(DONE_COLUMN, self.board)

        self.assertIsNotNone(task.started_at)
        self.assertIsNotNone(task.finished_at)
        self.assertTrue(task.finished)

    def test_started_at_is_not_overwritten_on_finish(self) -> None:
        earlier = datetime.now() - timedelta(days=3)
        task = Task(task_id=1, title="t", column_id=2, started_at=earlier)
        task.apply_column_transition(DONE_COLUMN, self.board)

        self.assertEqual(task.started_at, earlier)

    def test_reset_column_clears_both(self) -> None:
        task = Task(task_id=1, title="t", column_id=DONE_COLUMN, started_at=datetime.now(), finished_at=datetime.now())
        task.apply_column_transition(TODO_COLUMN, self.board)

        self.assertIsNone(task.started_at)
        self.assertIsNone(task.finished_at)
        self.assertFalse(task.finished)


class DependencyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.board = Board(board_id=1, name="B")
        self.backend = JsonBackend()
        self.blocker = Task(task_id=1, title="blocker", column_id=1)
        self.blocked = Task(task_id=2, title="blocked", column_id=1, blocked_by=[1])
        self.backend.create_task(self.blocker)
        self.backend.create_task(self.blocked)

    def test_unfinished_blocker_prevents_start(self) -> None:
        allowed, reason = self.blocked.can_move_to(IN_PROGRESS_COLUMN, self.board, self.backend)

        self.assertFalse(allowed)
        self.assertIn("blocker", reason)

    def test_finished_blocker_allows_start(self) -> None:
        self.backend.move_task(self.blocker, DONE_COLUMN)

        allowed, _ = self.blocked.can_move_to(IN_PROGRESS_COLUMN, self.board, self.backend)
        self.assertTrue(allowed)

    def test_ungated_columns_ignore_blockers(self) -> None:
        # Waiting is neither start nor finish, so a blocked card may sit there.
        allowed, _ = self.blocked.can_move_to(WAITING_COLUMN, self.board, self.backend)
        self.assertTrue(allowed)

    def test_task_without_blockers_is_never_gated(self) -> None:
        allowed, reason = self.blocker.can_move_to(DONE_COLUMN, self.board, self.backend)

        self.assertTrue(allowed)
        self.assertEqual(reason, "")


class DueDateTests(unittest.TestCase):
    def test_days_left_is_none_without_due_date(self) -> None:
        self.assertIsNone(Task(task_id=1, title="t", column_id=1).days_left)

    def test_days_left_counts_forward(self) -> None:
        task = Task(task_id=1, title="t", column_id=1, due_date=date.today() + timedelta(days=4))
        self.assertEqual(task.days_left, 4)

    def test_overdue_is_negative(self) -> None:
        task = Task(task_id=1, title="t", column_id=1, due_date=date.today() - timedelta(days=2))
        self.assertEqual(task.days_left, -2)


class MoveResultTests(unittest.TestCase):
    def test_failure_carries_no_task(self) -> None:
        result = MoveResult.failure("nope")

        self.assertFalse(result.ok)
        self.assertIsNone(result.task)
        self.assertEqual(result.message, "nope")

    def test_success_carries_the_task(self) -> None:
        task = Task(task_id=1, title="t", column_id=1)
        result = MoveResult.success(task)

        self.assertTrue(result.ok)
        self.assertIs(result.task, task)


if __name__ == "__main__":
    unittest.main()
