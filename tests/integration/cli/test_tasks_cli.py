"""``kbn task`` — putting cards on the board from the command line."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path

from pykantui.cli import main
from pykantui.config import BoardConfig, board_path
from pykantui.sync.jsonstore import JsonBackend


@contextlib.contextmanager
def sandbox() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        previous = os.environ.get("PYKANTUI_HOME")
        os.environ["PYKANTUI_HOME"] = directory
        try:
            yield Path(directory)
        finally:
            if previous is None:
                del os.environ["PYKANTUI_HOME"]
            else:
                os.environ["PYKANTUI_HOME"] = previous


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["task", *argv])
    return code, out.getvalue(), err.getvalue()


def board() -> JsonBackend:
    return JsonBackend(path=board_path(), config=BoardConfig.load())


def titles_in(column_name: str) -> list[str]:
    config = BoardConfig.load()
    column = config.find_by_name(column_name)
    assert column is not None
    return [task.title for task in board().get_tasks() if task.column_id == column.column_id]


class AddTests(unittest.TestCase):
    def test_a_single_card_keeps_the_title_as_given(self) -> None:
        with sandbox():
            code, output, _ = run("add", "Write the docs")
            titles = titles_in("To Do")

        self.assertEqual(code, 0)
        self.assertEqual(titles, ["Write the docs"])
        self.assertIn("added 1 card to To Do", output)

    def test_thirty_cards(self) -> None:
        with sandbox():
            code, output, _ = run("add", "Task", "--count", "30", "--column", "To Do")
            titles = titles_in("To Do")

        self.assertEqual(code, 0)
        self.assertEqual(len(titles), 30)
        self.assertIn("added 30 cards", output)

    def test_numbering_is_zero_padded_so_it_sorts(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "30")
            titles = titles_in("To Do")

        self.assertEqual(titles[0], "Task 01")
        self.assertEqual(titles[9], "Task 10")
        self.assertEqual(titles[29], "Task 30")
        self.assertEqual(sorted(titles), titles)

    def test_padding_follows_the_count(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "5")
            titles = titles_in("To Do")

        self.assertEqual(titles[0], "Task 1")

    def test_cards_land_in_order_with_contiguous_positions(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "12")
            positions = [task.position for task in board().get_tasks()]

        self.assertEqual(positions, list(range(12)))

    def test_ids_do_not_collide_across_runs(self) -> None:
        with sandbox():
            run("add", "First", "--count", "3")
            run("add", "Second", "--count", "3")
            ids = [task.task_id for task in board().get_tasks()]

        self.assertEqual(len(set(ids)), 6)

    def test_the_default_column_is_the_first_visible_one(self) -> None:
        with sandbox():
            run("add", "Somewhere")
            titles = titles_in("To Do")

        self.assertEqual(titles, ["Somewhere"])

    def test_a_column_can_be_named_or_numbered(self) -> None:
        with sandbox():
            run("add", "By name", "--column", "Done")
            run("add", "By id", "--column", "2")
            done = titles_in("Done")
            doing = titles_in("In Progress")

        self.assertEqual(done, ["By name"])
        self.assertEqual(doing, ["By id"])

    def test_an_unknown_column_is_refused(self) -> None:
        with sandbox():
            code, _, err = run("add", "X", "--column", "Nowhere")

        self.assertEqual(code, 1)
        self.assertIn("no column matching", err)

    def test_a_description_is_stored(self) -> None:
        with sandbox():
            run("add", "With body", "--description", "the details")
            task = board().get_tasks()[0]

        self.assertEqual(task.description, "the details")

    def test_zero_is_refused(self) -> None:
        with sandbox():
            code, _, err = run("add", "X", "--count", "0")

        self.assertEqual(code, 1)
        self.assertIn("at least 1", err)


class RemoveTests(unittest.TestCase):
    def test_removing_by_id(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "3")
            code, output, _ = run("rm", "2")
            remaining = [task.task_id for task in board().get_tasks()]

        self.assertEqual(code, 0)
        self.assertEqual(remaining, [1, 3])
        self.assertIn("deleted 1 card", output)

    def test_removing_several(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "5")
            run("rm", "1", "3", "5")
            remaining = [task.task_id for task in board().get_tasks()]

        self.assertEqual(remaining, [2, 4])

    def test_an_unknown_id_removes_nothing(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "3")
            code, _, err = run("rm", "1", "99")
            remaining = [task.task_id for task in board().get_tasks()]

        self.assertEqual(code, 1)
        self.assertIn("no card with id 99", err)
        self.assertEqual(remaining, [1, 2, 3])


class ClearTests(unittest.TestCase):
    def test_clear_needs_confirmation(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "4")
            code, _, err = run("clear", "To Do")
            remaining = titles_in("To Do")

        self.assertEqual(code, 1)
        self.assertIn("re-run with --yes", err)
        self.assertEqual(len(remaining), 4)

    def test_clear_leaves_other_columns_alone(self) -> None:
        with sandbox():
            run("add", "Task", "--count", "4")
            run("add", "Elsewhere", "--column", "Done")
            run("clear", "To Do", "--yes")
            todo = titles_in("To Do")
            done = titles_in("Done")

        self.assertEqual(todo, [])
        self.assertEqual(done, ["Elsewhere"])

    def test_clearing_an_empty_column_is_not_an_error(self) -> None:
        with sandbox():
            code, output, _ = run("clear", "Done")

        self.assertEqual(code, 0)
        self.assertIn("already empty", output)


if __name__ == "__main__":
    unittest.main()
