"""``kbn columns`` — the commands that save the board shape."""

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
from pykantui.models import Task
from pykantui.sync.jsonstore import JsonBackend


@contextlib.contextmanager
def sandbox() -> Iterator[Path]:
    """Point PYKANTUI_HOME at a throwaway directory."""
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
        code = main(["columns", *argv])
    return code, out.getvalue(), err.getvalue()


def names() -> list[str]:
    return [column.name for column in BoardConfig.load().ordered()]


def visible_names() -> list[str]:
    return [column.name for column in BoardConfig.load().ordered() if column.visible]


class ListTests(unittest.TestCase):
    def test_list_shows_every_column_with_its_role(self) -> None:
        with sandbox():
            code, output, _ = run("list")

        self.assertEqual(code, 0)
        self.assertIn("To Do", output)
        self.assertIn("Needs Review", output)
        self.assertIn("finish", output)

    def test_list_is_the_default_action(self) -> None:
        with sandbox():
            _, explicit, _ = run("list")
        with sandbox():
            _, implicit, _ = run()

        self.assertEqual(explicit, implicit)


class AddTests(unittest.TestCase):
    def test_add_appends_and_saves(self) -> None:
        with sandbox():
            code, _, _ = run("add", "Blocked")
            saved = names()

        self.assertEqual(code, 0)
        self.assertEqual(saved[-1], "Blocked")

    def test_add_after_a_named_column(self) -> None:
        with sandbox():
            run("add", "Triage", "--after", "To Do")
            saved = names()

        self.assertEqual(saved[:3], ["To Do", "Triage", "In Progress"])

    def test_add_with_statuses(self) -> None:
        with sandbox():
            run("add", "Blocked", "--statuses", "BLOCKED, ON ICE")
            column = BoardConfig.load().find_by_name("Blocked")

        assert column is not None
        self.assertEqual(column.jira_statuses, ["BLOCKED", "ON ICE"])

    def test_duplicate_names_are_refused(self) -> None:
        with sandbox():
            code, _, err = run("add", "To Do")

        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_an_unknown_after_target_is_refused(self) -> None:
        with sandbox():
            code, _, err = run("add", "X", "--after", "Nowhere")

        self.assertEqual(code, 1)
        self.assertIn("no column matching", err)


class RenameAndMoveTests(unittest.TestCase):
    def test_rename(self) -> None:
        with sandbox():
            run("rename", "Waiting", "On Hold")
            saved = names()

        self.assertIn("On Hold", saved)
        self.assertNotIn("Waiting", saved)

    def test_move_to_the_front(self) -> None:
        with sandbox():
            run("move", "Done", "1")
            saved = names()

        self.assertEqual(saved[0], "Done")

    def test_move_keeps_the_finish_role_on_the_same_column(self) -> None:
        with sandbox():
            before = BoardConfig.load().finish_column
            run("move", "Done", "1")
            after = BoardConfig.load().finish_column

        self.assertEqual(before, after)


class RemoveTests(unittest.TestCase):
    def test_remove_drops_the_column(self) -> None:
        with sandbox():
            run("remove", "Waiting")
            saved = names()

        self.assertNotIn("Waiting", saved)

    def test_remove_rehomes_the_cards(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            waiting = config.find_by_name("Waiting")
            assert waiting is not None
            backend = JsonBackend(path=board_path(), config=config)
            backend.create_task(Task(task_id=1, title="parked", column_id=waiting.column_id))

            _, output, _ = run("remove", "Waiting")

            reloaded = JsonBackend(path=board_path(), config=BoardConfig.load())
            task = reloaded.get_task_by_id(1)

        assert task is not None
        self.assertNotEqual(task.column_id, waiting.column_id)
        self.assertIn("moved 1 card", output)

    def test_remove_can_choose_where_the_cards_go(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            waiting = config.find_by_name("Waiting")
            done = config.find_by_name("Done")
            assert waiting is not None and done is not None
            backend = JsonBackend(path=board_path(), config=config)
            backend.create_task(Task(task_id=1, title="parked", column_id=waiting.column_id))

            run("remove", "Waiting", "--move-to", "Done")

            reloaded = JsonBackend(path=board_path(), config=BoardConfig.load())
            task = reloaded.get_task_by_id(1)

        assert task is not None
        self.assertEqual(task.column_id, done.column_id)

    def test_removing_a_role_column_says_so(self) -> None:
        with sandbox():
            _, output, _ = run("remove", "Done")

        self.assertIn("finish column", output)

    def test_the_last_visible_column_cannot_be_removed(self) -> None:
        with sandbox():
            run("count", "1")
            code, _, err = run("remove", "To Do")

        self.assertEqual(code, 1)
        self.assertIn("at least one column", err)


class CountTests(unittest.TestCase):
    def test_growing_to_ten(self) -> None:
        with sandbox():
            code, _, _ = run("count", "10")
            saved = visible_names()

        self.assertEqual(code, 0)
        self.assertEqual(len(saved), 10)
        self.assertEqual(saved[:5], ["To Do", "In Progress", "Needs Review", "Waiting", "Done"])

    def test_shrinking_to_three_keeps_the_leftmost(self) -> None:
        with sandbox():
            run("count", "3")
            saved = visible_names()

        self.assertEqual(saved, ["To Do", "In Progress", "Needs Review"])

    def test_shrinking_moves_the_stranded_cards_left(self) -> None:
        with sandbox():
            config = BoardConfig.load()
            done = config.find_by_name("Done")
            assert done is not None
            backend = JsonBackend(path=board_path(), config=config)
            backend.create_task(Task(task_id=1, title="finished", column_id=done.column_id))

            run("count", "3")

            reloaded = JsonBackend(path=board_path(), config=BoardConfig.load())
            task = reloaded.get_task_by_id(1)
            remaining = [column.column_id for column in BoardConfig.load().ordered()]

        assert task is not None
        self.assertIn(task.column_id, remaining)

    def test_zero_is_refused(self) -> None:
        with sandbox():
            code, _, err = run("count", "0")

        self.assertEqual(code, 1)
        self.assertIn("at least one column", err)

    def test_count_is_idempotent(self) -> None:
        with sandbox():
            run("count", "6")
            first = names()
            run("count", "6")
            second = names()

        self.assertEqual(first, second)


class RoleTests(unittest.TestCase):
    def test_setting_the_finish_column(self) -> None:
        with sandbox():
            run("add", "Shipped")
            run("role", "finish", "Shipped")
            config = BoardConfig.load()
            shipped = config.find_by_name("Shipped")

        assert shipped is not None
        self.assertEqual(config.finish_column, shipped.column_id)

    def test_clearing_a_role(self) -> None:
        with sandbox():
            run("role", "finish")
            config = BoardConfig.load()

        self.assertIsNone(config.finish_column)

    def test_an_unknown_column_is_refused(self) -> None:
        with sandbox():
            code, _, err = run("role", "start", "Nowhere")

        self.assertEqual(code, 1)
        self.assertIn("no column matching", err)


class StatusTests(unittest.TestCase):
    def test_setting_statuses(self) -> None:
        with sandbox():
            run("statuses", "Waiting", "BLOCKED, ON ICE")
            column = BoardConfig.load().find_by_name("Waiting")

        assert column is not None
        self.assertEqual(column.jira_statuses, ["BLOCKED", "ON ICE"])

    def test_clearing_statuses(self) -> None:
        with sandbox():
            run("statuses", "Waiting", "")
            column = BoardConfig.load().find_by_name("Waiting")

        assert column is not None
        self.assertEqual(column.jira_statuses, [])

    def test_a_status_cannot_belong_to_two_columns(self) -> None:
        with sandbox():
            code, _, err = run("statuses", "Waiting", "IN PROGRESS")
            column = BoardConfig.load().find_by_name("Waiting")

        self.assertEqual(code, 1)
        self.assertIn("already mapped", err)
        assert column is not None
        self.assertNotIn("IN PROGRESS", column.jira_statuses)

    def test_the_clash_check_ignores_case(self) -> None:
        with sandbox():
            code, _, _ = run("statuses", "Waiting", "in progress")

        self.assertEqual(code, 1)


class VisibilityTests(unittest.TestCase):
    def test_hide_and_show(self) -> None:
        with sandbox():
            run("hide", "Waiting")
            hidden = BoardConfig.load().find_by_name("Waiting")
            assert hidden is not None
            was_hidden = hidden.visible

            run("show", "Waiting")
            shown = BoardConfig.load().find_by_name("Waiting")

        self.assertFalse(was_hidden)
        assert shown is not None
        self.assertTrue(shown.visible)

    def test_the_last_visible_column_cannot_be_hidden(self) -> None:
        with sandbox():
            run("count", "1")
            code, _, err = run("hide", "1")

        self.assertEqual(code, 1)
        self.assertIn("at least one column", err)


class ResetTests(unittest.TestCase):
    def test_reset_needs_confirmation(self) -> None:
        with sandbox():
            run("add", "Extra")
            code, _, _ = run("reset")
            saved = names()

        self.assertEqual(code, 1)
        self.assertIn("Extra", saved)

    def test_reset_with_yes_restores_the_default(self) -> None:
        with sandbox():
            run("add", "Extra")
            run("count", "9")
            code, _, _ = run("reset", "--yes")
            saved = names()

        self.assertEqual(code, 0)
        self.assertEqual(saved, ["To Do", "In Progress", "Needs Review", "Waiting", "Done", "Archive"])


if __name__ == "__main__":
    unittest.main()
