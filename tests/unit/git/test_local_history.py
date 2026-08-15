"""Local Git is an isolated, mandatory safety net around provider writes."""

from __future__ import annotations

import importlib.util
import io
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from pykantui import git
from pykantui.commands.sync import _confirm_for
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.errors import ProviderError
from pykantui.workspace.sync import sync
from tests.integration.sync.test_push import DOING, PROJECT, TODO, RecordingProvider, issue


class GitPackageStructureTests(unittest.TestCase):
    def test_git_is_a_package_not_a_root_implementation_module(self) -> None:
        spec = importlib.util.find_spec("pykantui.git")

        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.submodule_search_locations if spec else None)

    def test_public_git_api_has_no_remote_operations(self) -> None:
        for operation in ("fetch", "pull", "push", "remote"):
            with self.subTest(operation=operation):
                self.assertFalse(hasattr(git, operation))

    def test_a_timed_out_git_command_fails_closed(self) -> None:
        error = subprocess.TimeoutExpired(["git"], timeout=1)
        with patch("pykantui.git.repository.run_git", side_effect=error):
            self.assertFalse(git.add_all(Path("workspace")))
            self.assertFalse(git.commit(Path("workspace"), "checkpoint"))

    def test_a_timed_out_status_is_not_misreported_as_clean(self) -> None:
        error = subprocess.TimeoutExpired(["git"], timeout=1)
        with (
            patch("pykantui.git.repository.run_git", side_effect=error),
            self.assertRaisesRegex(git.GitCommandError, "inspect local Git status"),
        ):
            git.status(Path("workspace"))

    def test_commit_fails_closed_if_status_breaks_after_staging(self) -> None:
        with (
            patch("pykantui.git.repository._add_paths", return_value=True),
            patch(
                "pykantui.git.repository.is_dirty",
                side_effect=git.GitCommandError("could not inspect local Git status"),
            ),
        ):
            committed = git.commit(
                Path("workspace"),
                "checkpoint",
                paths=(Path("workspace") / ".pykantui",),
            )

        self.assertFalse(committed)

    def test_runtime_lock_metadata_is_not_part_of_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.assertTrue(git.init(workspace))
            metadata = workspace / ".pykantui" / "sync.lock.owner.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text('{"pid": 1}', encoding="utf-8")

            self.assertFalse(git.is_dirty(workspace))
            self.assertFalse(git.commit(workspace, "must not checkpoint runtime metadata"))

    def test_missing_git_is_reported_before_a_versioned_provider_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".git").mkdir()
            provider = RecordingProvider([issue("K-1", TODO)])

            with (
                patch("pykantui.workspace.checkpoints.git.available", return_value=False),
                self.assertRaisesRegex(ProviderError, "Git is unavailable"),
            ):
                sync(workspace, provider, PROJECT, commit=True)

            self.assertEqual([], provider.updates)

    def test_unreadable_git_status_stops_before_a_provider_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".git").mkdir()
            provider = RecordingProvider([issue("K-1", TODO)])

            with (
                patch("pykantui.workspace.checkpoints.git.available", return_value=True),
                patch("pykantui.workspace.checkpoints.git.ensure_runtime_ignored", return_value=True),
                patch("pykantui.workspace.checkpoints.git.is_repo", return_value=True),
                patch(
                    "pykantui.workspace.checkpoints.git.is_dirty",
                    side_effect=git.GitCommandError("could not inspect local Git status"),
                ),
                self.assertRaisesRegex(ProviderError, "inspect local Git status"),
            ):
                sync(workspace, provider, PROJECT, commit=True)

            self.assertEqual([], provider.updates)

    def test_status_failure_inside_commit_stops_before_a_provider_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".git").mkdir()
            provider = RecordingProvider([issue("K-1", TODO)])

            with (
                patch("pykantui.workspace.checkpoints.git.available", return_value=True),
                patch("pykantui.workspace.checkpoints.git.ensure_runtime_ignored", return_value=True),
                patch("pykantui.workspace.checkpoints.git.is_repo", return_value=True),
                patch("pykantui.workspace.checkpoints.git.is_dirty", return_value=True),
                patch("pykantui.git.repository._add_paths", return_value=True),
                patch(
                    "pykantui.git.repository.is_dirty",
                    side_effect=git.GitCommandError("could not inspect local Git status"),
                ),
                self.assertRaisesRegex(ProviderError, "before-sync version"),
            ):
                sync(workspace, provider, PROJECT, commit=True)

            self.assertEqual([], provider.updates)


def command(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@unittest.skipUnless(git.available(), "git is not installed")
class RepositoryIsolationTests(unittest.TestCase):
    def test_workspace_inside_another_repo_gets_its_own_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "parent"
            workspace = parent / "boards" / "project"
            workspace.mkdir(parents=True)
            self.assertTrue(git.init(parent))

            self.assertTrue(git.init(workspace))

            self.assertTrue((workspace / ".git").exists())
            self.assertEqual(workspace.resolve(), Path(command(workspace, "rev-parse", "--show-toplevel")).resolve())

    def test_scoped_commit_never_stages_preexisting_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            private = workspace / "private.pem"
            private.write_text("not-a-real-secret", encoding="utf-8")
            owned = workspace / ".pykantui" / "project.json"
            owned.parent.mkdir(parents=True)
            owned.write_text("{}", encoding="utf-8")
            self.assertTrue(git.init(workspace))

            self.assertTrue(git.commit(workspace, "owned only", paths=(owned,)))

            tracked = command(workspace, "ls-files").splitlines()
            self.assertEqual([".pykantui/project.json"], tracked)
            self.assertIn("private.pem", command(workspace, "status", "--porcelain"))


@unittest.skipUnless(git.available(), "git is not installed")
class SyncCheckpointTests(unittest.TestCase):
    def test_sync_versions_local_intent_before_provider_and_result_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            provider = RecordingProvider([issue("K-1", TODO)])
            self.assertTrue(git.init(workspace))
            sync(workspace, provider, PROJECT, push_edits=False, commit=True)
            backend = ProviderBackend(workspace, provider, PROJECT)
            card = backend.get_tasks()[0]
            target = next(column for column in backend.get_columns() if column.name == DOING.name)
            backend.move_task(card, target.column_id)

            backend.sync_now(confirm=lambda plan: True, commit=True)

            messages = command(workspace, "log", "--format=%s").splitlines()
            self.assertTrue(any("before sync" in message for message in messages), messages)
            self.assertTrue(any("after sync" in message for message in messages), messages)
            self.assertEqual("", command(workspace, "status", "--porcelain"))


class NonInteractiveConfirmationTests(unittest.TestCase):
    def args(self, *, yes: bool = False) -> Namespace:
        return Namespace(pull_only=False, dry_run=False, yes=yes)

    def test_redirected_stdin_does_not_implicitly_approve_provider_writes(self) -> None:
        with patch("pykantui.commands.sync.sys.stdin", io.StringIO()):
            confirm = _confirm_for(self.args())

        self.assertIsNotNone(confirm)

    def test_yes_is_the_explicit_noninteractive_approval(self) -> None:
        with patch("pykantui.commands.sync.sys.stdin", io.StringIO()):
            confirm = _confirm_for(self.args(yes=True))

        self.assertIsNone(confirm)


if __name__ == "__main__":
    unittest.main()
