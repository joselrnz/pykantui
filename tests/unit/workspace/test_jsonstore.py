"""The local JSON backend."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pykantui.models import Task
from pykantui.sync.jsonstore import JsonBackend, demo_backend


class MoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = JsonBackend()
        for index in range(3):
            self.backend.create_task(Task(task_id=index + 1, title=f"t{index}", column_id=1))

    def test_move_appends_to_the_destination(self) -> None:
        self.backend.create_task(Task(task_id=9, title="already there", column_id=2))

        result = self.backend.move_task(self.backend.get_tasks()[0], 2)

        self.assertTrue(result.ok)
        assert result.task is not None
        self.assertEqual(result.task.column_id, 2)
        self.assertEqual(result.task.position, 1)

    def test_move_renumbers_the_origin_column(self) -> None:
        self.backend.move_task(self.backend.get_tasks()[0], 2)

        remaining = self.backend.tasks_in_column(1)
        self.assertEqual([task.position for task in remaining], [0, 1])

    def test_move_to_explicit_position(self) -> None:
        self.backend.create_task(Task(task_id=9, title="first", column_id=2))

        result = self.backend.move_task(self.backend.get_tasks()[0], 2, target_position=0)

        assert result.task is not None
        self.assertEqual(result.task.position, 0)
        self.assertEqual([task.task_id for task in self.backend.tasks_in_column(2)], [1, 9])

    def test_move_to_unknown_column_fails_without_mutating(self) -> None:
        before = [task.column_id for task in self.backend.get_tasks()]

        result = self.backend.move_task(self.backend.get_tasks()[0], 99)

        self.assertFalse(result.ok)
        self.assertIn("99", result.message)
        self.assertEqual([task.column_id for task in self.backend.get_tasks()], before)

    def test_move_of_unknown_task_fails(self) -> None:
        result = self.backend.move_task(Task(task_id=404, title="ghost", column_id=1), 2)

        self.assertFalse(result.ok)
        self.assertIsNone(result.task)


class ReorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = JsonBackend()
        for index in range(3):
            self.backend.create_task(Task(task_id=index + 1, title=f"t{index}", column_id=1))

    def test_reorder_to_top(self) -> None:
        self.backend.reorder_task(self.backend.get_tasks()[2], 0)

        self.assertEqual([task.task_id for task in self.backend.tasks_in_column(1)], [3, 1, 2])

    def test_reorder_past_the_end_clamps(self) -> None:
        self.backend.reorder_task(self.backend.get_tasks()[0], 99)

        self.assertEqual([task.task_id for task in self.backend.tasks_in_column(1)], [2, 3, 1])

    def test_positions_stay_contiguous(self) -> None:
        self.backend.reorder_task(self.backend.get_tasks()[0], 2)

        self.assertEqual([task.position for task in self.backend.tasks_in_column(1)], [0, 1, 2])


class CrudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = JsonBackend()

    def test_create_assigns_the_next_free_position(self) -> None:
        self.backend.create_task(Task(task_id=1, title="a", column_id=1))
        result = self.backend.create_task(Task(task_id=2, title="b", column_id=1))

        assert result.task is not None
        self.assertEqual(result.task.position, 1)

    def test_duplicate_id_is_rejected(self) -> None:
        self.backend.create_task(Task(task_id=1, title="a", column_id=1))

        result = self.backend.create_task(Task(task_id=1, title="clash", column_id=1))
        self.assertFalse(result.ok)

    def test_delete_removes_dangling_dependencies(self) -> None:
        self.backend.create_task(Task(task_id=1, title="blocker", column_id=1))
        self.backend.create_task(Task(task_id=2, title="blocked", column_id=1, blocked_by=[1]))

        self.backend.delete_task(1)

        remaining = self.backend.get_task_by_id(2)
        assert remaining is not None
        self.assertEqual(remaining.blocked_by, [])

    def test_next_task_id_skips_used_ids(self) -> None:
        self.backend.create_task(Task(task_id=7, title="a", column_id=1))
        self.assertEqual(self.backend.next_task_id(), 8)

    def test_get_tasks_by_ids_is_a_single_pass(self) -> None:
        for index in range(5):
            self.backend.create_task(Task(task_id=index + 1, title=f"t{index}", column_id=1))

        found = self.backend.get_tasks_by_ids([2, 4])
        self.assertEqual([task.task_id for task in found], [2, 4])


class PersistenceTests(unittest.TestCase):
    def test_round_trip_through_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.json"
            backend = JsonBackend(path=path)
            backend.create_task(Task(task_id=1, title="persisted", column_id=1, description="body"))
            backend.move_task(backend.get_tasks()[0], 2)

            reloaded = JsonBackend(path=path)
            tasks = reloaded.get_tasks()

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "persisted")
        self.assertEqual(tasks[0].column_id, 2)
        self.assertIsNotNone(tasks[0].started_at)

    def test_demo_board_fills_every_visible_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = demo_backend(Path(directory) / "demo.json")
            counts = {
                column.column_id: len(backend.tasks_in_column(column.column_id))
                for column in backend.get_visible_columns()
            }

        self.assertEqual(counts, {1: 3, 2: 1, 3: 1, 4: 1, 5: 1})

    def test_archive_column_is_hidden_by_default(self) -> None:
        visible = [column.column_id for column in JsonBackend().get_visible_columns()]
        self.assertEqual(visible, [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()


class AtomicSaveTests(unittest.TestCase):
    """Saving must never leave a half-written board behind.

    ``save()`` runs on every mutation, so the window between truncating and
    writing was one the user could realistically land in.
    """

    def test_save_replaces_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "board.json"
            backend = JsonBackend(path=path)
            backend.create_task(Task(task_id=1, title="first", column_id=1))

            backend.create_task(Task(task_id=2, title="second", column_id=1))

            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([task["title"] for task in document["tasks"]], ["first", "second"])

    def test_failed_save_leaves_the_previous_board_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "board.json"
            backend = JsonBackend(path=path)
            backend.create_task(Task(task_id=1, title="keep me", column_id=1))

            with (
                patch("pykantui.config.paths.os.replace", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                backend.create_task(Task(task_id=2, title="lost", column_id=1))

            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([task["title"] for task in document["tasks"]], ["keep me"])

    def test_failed_save_leaves_no_temp_file_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "board.json"
            backend = JsonBackend(path=path)
            backend.create_task(Task(task_id=1, title="first", column_id=1))

            with (
                patch("pykantui.config.paths.os.replace", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                backend.create_task(Task(task_id=2, title="second", column_id=1))

            self.assertEqual([p.name for p in Path(temp_dir).iterdir()], ["board.json"])
