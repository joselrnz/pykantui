"""The board shape as configuration.

Nothing in here assumes a particular number of columns — that is the point of
the tests.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pykantui.config import BoardConfig, ColumnConfig, default_config, write_text_atomic
from pykantui.models import Task
from pykantui.sync.jsonstore import JsonBackend


def board_of(*names: str) -> BoardConfig:
    return BoardConfig(
        columns=[ColumnConfig(column_id=index + 1, name=name, position=index) for index, name in enumerate(names)],
        reset_column=1,
        start_column=2 if len(names) > 1 else None,
        finish_column=len(names) if names else None,
    )


class ShapeTests(unittest.TestCase):
    def test_a_board_can_have_one_column(self) -> None:
        config = board_of("Everything")

        self.assertEqual(len(config.to_columns()), 1)

    def test_a_board_can_have_twelve(self) -> None:
        config = board_of(*[f"Stage {index}" for index in range(12)])

        columns = config.to_columns()
        self.assertEqual(len(columns), 12)
        self.assertEqual([column.position for column in columns], list(range(12)))

    def test_add_appends_by_default(self) -> None:
        config = board_of("A", "B")

        config.add("C")

        self.assertEqual([column.name for column in config.ordered()], ["A", "B", "C"])

    def test_add_after_inserts_in_the_middle(self) -> None:
        config = board_of("A", "C")

        config.add("B", after=config.find_by_name("A"))

        self.assertEqual([column.name for column in config.ordered()], ["A", "B", "C"])

    def test_added_columns_get_a_fresh_id(self) -> None:
        config = board_of("A", "B")

        added = config.add("C")

        self.assertEqual(added.column_id, 3)
        self.assertEqual(len({column.column_id for column in config.columns}), 3)

    def test_ids_are_not_reused_after_a_delete(self) -> None:
        config = board_of("A", "B", "C")
        config.remove(config.find_by_name("B"))  # type: ignore[arg-type]

        added = config.add("D")

        self.assertEqual(added.column_id, 4)

    def test_positions_stay_contiguous_after_a_delete(self) -> None:
        config = board_of("A", "B", "C")

        config.remove(config.find_by_name("A"))  # type: ignore[arg-type]

        self.assertEqual([column.position for column in config.ordered()], [0, 1])

    def test_move_shifts_the_others_along(self) -> None:
        config = board_of("A", "B", "C")

        config.move(config.find_by_name("C"), 1)  # type: ignore[arg-type]

        self.assertEqual([column.name for column in config.ordered()], ["C", "A", "B"])

    def test_move_past_the_end_clamps(self) -> None:
        config = board_of("A", "B", "C")

        config.move(config.find_by_name("A"), 99)  # type: ignore[arg-type]

        self.assertEqual([column.name for column in config.ordered()], ["B", "C", "A"])


class RoleTests(unittest.TestCase):
    def test_roles_survive_reordering(self) -> None:
        config = board_of("A", "B", "C")
        finish_id = config.finish_column

        config.move(config.find_by_name("C"), 1)  # type: ignore[arg-type]

        # Roles are column ids, so shuffling the board does not change which
        # column means "finished".
        self.assertEqual(config.finish_column, finish_id)

    def test_deleting_a_role_column_clears_the_role(self) -> None:
        config = board_of("A", "B", "C")

        config.remove(config.find_by_name("C"))  # type: ignore[arg-type]

        self.assertIsNone(config.finish_column)

    def test_role_of_names_the_role(self) -> None:
        config = board_of("A", "B", "C")

        self.assertEqual(config.role_of(1), "reset")
        self.assertEqual(config.role_of(2), "start")
        self.assertEqual(config.role_of(3), "finish")

    def test_role_of_is_none_for_a_plain_column(self) -> None:
        config = board_of("A", "B", "C")
        config.add("Waiting")

        self.assertIsNone(config.role_of(4))

    def test_a_board_with_no_finish_column_is_allowed(self) -> None:
        config = board_of("A", "B")
        config.set_role("finish", None)

        backend = JsonBackend(config=config)
        backend.create_task(Task(task_id=1, title="t", column_id=1))
        result = backend.move_task(backend.get_tasks()[0], 2)

        self.assertTrue(result.ok)


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = board_of("To Do", "In Progress", "Done")

    def test_by_id(self) -> None:
        found = self.config.resolve("2")
        assert found is not None
        self.assertEqual(found.name, "In Progress")

    def test_by_name_ignoring_case(self) -> None:
        found = self.config.resolve("in progress")
        assert found is not None
        self.assertEqual(found.column_id, 2)

    def test_by_position_when_no_id_matches(self) -> None:
        config = board_of("A", "B")
        config.remove(config.find_by_name("A"))  # type: ignore[arg-type]
        config.add("C")  # ids are now 2 and 3

        found = config.resolve("1")  # no column #1, so read it as a position
        assert found is not None
        self.assertEqual(found.name, "B")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(self.config.resolve("nope"))


class PersistenceTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = board_of("A", "B", "C")
            config.add("D", statuses=["ONE", "TWO"])
            config.save(path)

            reloaded = BoardConfig.load(path)

        self.assertEqual([column.name for column in reloaded.ordered()], ["A", "B", "C", "D"])
        self.assertEqual(reloaded.find_by_name("D").jira_statuses, ["ONE", "TWO"])  # type: ignore[union-attr]
        self.assertEqual(reloaded.reset_column, config.reset_column)

    def test_first_load_writes_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"

            config = BoardConfig.load(path)

            self.assertTrue(path.exists())
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(len(config.columns), len(document["columns"]))
        self.assertEqual("cyberpunk", config.theme)
        self.assertEqual("cyberpunk", document["theme"])

    def test_the_default_carries_the_jira_statuses(self) -> None:
        config = default_config()

        mapping = config.jira_column_mapping()
        self.assertEqual(mapping["IN PROGRESS"], config.start_column)
        self.assertEqual(mapping["CANCEL"], config.finish_column)


class BackendWiringTests(unittest.TestCase):
    def test_the_json_backend_takes_its_columns_from_the_config(self) -> None:
        backend = JsonBackend(config=board_of(*[f"S{index}" for index in range(7)]))

        self.assertEqual(len(backend.get_visible_columns()), 7)

    def test_moving_to_a_column_that_is_not_configured_fails(self) -> None:
        backend = JsonBackend(config=board_of("A", "B"))
        backend.create_task(Task(task_id=1, title="t", column_id=1))

        result = backend.move_task(backend.get_tasks()[0], 99)

        self.assertFalse(result.ok)

    def test_hidden_columns_are_still_valid_targets(self) -> None:
        config = board_of("A", "B")
        config.find_by_name("B").visible = False  # type: ignore[union-attr]
        backend = JsonBackend(config=config)
        backend.create_task(Task(task_id=1, title="t", column_id=1))

        result = backend.move_task(backend.get_tasks()[0], 2)

        self.assertTrue(result.ok)
        self.assertEqual(len(backend.get_visible_columns()), 1)


class ReloadTests(unittest.TestCase):
    def test_reload_config_picks_up_a_new_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("PYKANTUI_HOME")
            os.environ["PYKANTUI_HOME"] = directory
            try:
                backend = JsonBackend(config=BoardConfig.load())
                before = len(backend.get_columns())

                config = BoardConfig.load()
                config.add("Blocked")
                config.save()

                backend.reload_config()
                after = len(backend.get_columns())
            finally:
                if previous is None:
                    del os.environ["PYKANTUI_HOME"]
                else:
                    os.environ["PYKANTUI_HOME"] = previous

        self.assertEqual(after, before + 1)


if __name__ == "__main__":
    unittest.main()


class SaveSafetyTests(unittest.TestCase):
    """An in-memory config must never write to the real config file.

    This is a regression test with a real incident behind it: collapsing a
    column in a test rewrote the user's actual board shape, because save()
    with no path fell back to config_path().
    """

    def test_an_in_memory_config_does_not_write_anywhere(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("PYKANTUI_HOME")
            os.environ["PYKANTUI_HOME"] = directory
            try:
                config = board_of("A", "B")
                config.save()  # no path, never loaded from disk

                written = list(Path(directory).iterdir())
            finally:
                if previous is None:
                    del os.environ["PYKANTUI_HOME"]
                else:
                    os.environ["PYKANTUI_HOME"] = previous

        self.assertEqual(written, [])

    def test_collapsing_a_column_on_a_demo_board_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("PYKANTUI_HOME")
            os.environ["PYKANTUI_HOME"] = directory
            try:
                backend = JsonBackend(config=board_of("A", "B"))
                backend.set_column_collapsed(1, True)

                written = list(Path(directory).iterdir())
            finally:
                if previous is None:
                    del os.environ["PYKANTUI_HOME"]
                else:
                    os.environ["PYKANTUI_HOME"] = previous

        self.assertEqual(written, [])

    def test_a_loaded_config_saves_back_to_where_it_came_from(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elsewhere.json"
            board_of("A", "B").save(path)

            config = BoardConfig.load(path)
            config.add("C")
            config.save()  # no argument: back to the same file

            reloaded = BoardConfig.load(path)

        self.assertEqual([column.name for column in reloaded.ordered()], ["A", "B", "C"])

    def test_an_explicit_path_still_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "one.json"
            second = Path(directory) / "two.json"
            board_of("A").save(first)

            config = BoardConfig.load(first)
            config.save(second)

            self.assertTrue(second.exists())


class CredentialFilePermissionTests(unittest.TestCase):
    """The starter Jira config is where the user is told to paste an API token.

    It has to be owner-only from the moment it is created -- nobody goes back
    and tightens a file after the fact.
    """

    @unittest.skipIf(os.name == "nt", "POSIX file modes; Windows chmod only toggles read-only")
    def test_private_write_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jira.json"

            write_text_atomic(path, "{}", private=True)

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX file modes; Windows chmod only toggles read-only")
    def test_ordinary_writes_are_owner_accessible_and_not_world_writable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "board.json"

            write_text_atomic(path, "{}")

            mode = path.stat().st_mode & 0o777
            self.assertEqual(0o600, mode & 0o600)
            self.assertEqual(0, mode & 0o002)

    def test_private_write_chmods_before_the_file_is_visible(self) -> None:
        # Runs on every platform by forcing the POSIX branch, so the mode is
        # actually asserted rather than skipped on the machine doing the work.
        # chmod lands on the temp file, so the target is never briefly readable.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jira.json"
            with patch.object(os, "name", "posix"), patch.object(Path, "chmod") as chmod:
                write_text_atomic(path, "{}", private=True)

        self.assertEqual([0o700, 0o600], [record.args[0] for record in chmod.call_args_list])
        self.assertNotEqual(chmod.call_args.args, (path,))

    def test_ordinary_write_does_not_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "board.json"
            with patch.object(os, "name", "posix"), patch.object(Path, "chmod") as chmod:
                write_text_atomic(path, "{}")

        chmod.assert_not_called()

    def test_private_directory_uses_native_windows_acl_without_chmod(self) -> None:
        from pykantui.config.paths import ensure_private_directory

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "private"
            with patch.object(os, "name", "nt"), patch.object(Path, "chmod") as chmod:
                ensure_private_directory(path)

        chmod.assert_not_called()


class AtomicConfigSaveTests(unittest.TestCase):
    def test_failed_save_leaves_the_previous_shape_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            board_of("To Do", "Done").save(path)

            with (
                patch("pykantui.config.paths.os.replace", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                board_of("Wrecked").save(path)

            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([column["name"] for column in document["columns"]], ["To Do", "Done"])
