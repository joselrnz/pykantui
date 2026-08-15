"""Safe deletion contracts for unsent provider Markdown drafts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pykantui.models import Task
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.registry import get
from pykantui.workspace import layout
from pykantui.workspace.state import SyncState
from pykantui.workspace.sync import sync
from tests.integration.sync.test_push import PROJECT, TODO, RecordingProvider, issue


class DraftDeleteCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.provider = RecordingProvider([issue("JPT-1", TODO, title="Synced card")])
        self.provider.spec = get("jira").spec  # type: ignore[misc]
        sync(self.workspace, self.provider, PROJECT, push_edits=False, commit=False)
        self.backend = ProviderBackend(self.workspace, self.provider, PROJECT)
        self.provider.updates.clear()
        self.provider.moves.clear()
        self.provider.fetches.clear()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_draft(self, title: str = "Unsent local draft") -> Task:
        created = self.backend.create_task(Task(task_id=999, title=title, column_id=1))
        self.assertTrue(created.ok, created.message)
        assert created.task is not None
        return created.task

    def provider_calls(self) -> tuple[int, int, int]:
        return len(self.provider.updates), len(self.provider.moves), len(self.provider.fetches)


class DraftDeleteBackendTests(DraftDeleteCase):
    def test_only_owned_valid_unsynced_draft_is_deletable(self) -> None:
        synced = next(item for item in self.backend.get_tasks() if item.metadata["key"] == "JPT-1")
        draft = self.make_draft()

        self.assertFalse(self.backend.can_delete_task(synced))
        self.assertTrue(self.backend.can_delete_task(draft))
        self.assertTrue(self.backend.can_delete_tasks())

        self.backend._theirs.add(draft.task_id)  # noqa: SLF001 - team-card boundary
        self.assertFalse(self.backend.can_delete_task(draft))
        self.backend._theirs.remove(draft.task_id)  # noqa: SLF001

        path = self.backend._paths[draft.task_id]  # noqa: SLF001
        path.write_text(path.read_text(encoding="utf-8").replace("title:", "title: broken\ntitle:"), encoding="utf-8")
        self.backend.reload_local()
        invalid = next(item for item in self.backend.get_tasks() if item.metadata["sync_status"] == "invalid")
        self.assertFalse(self.backend.can_delete_task(invalid))
        self.assertEqual((0, 0, 0), self.provider_calls())

    def test_delete_moves_exact_draft_to_workspace_trash_and_never_calls_provider(self) -> None:
        draft = self.make_draft("Recoverable draft")
        original = self.backend._paths[draft.task_id]  # noqa: SLF001
        content = original.read_bytes()

        result = self.backend.delete_task_if_current(draft)

        self.assertTrue(result.ok, result.message)
        self.assertIn("trash", result.message.lower())
        self.assertFalse(original.exists())
        quarantined = list(layout.trash_dir(self.workspace).rglob("*.md"))
        self.assertEqual(1, len(quarantined))
        self.assertEqual(content, quarantined[0].read_bytes())
        self.assertNotIn(draft.metadata["id"], {item.metadata["id"] for item in self.backend.get_tasks()})
        self.assertEqual((0, 0, 0), self.provider_calls())

    def test_synced_conflict_and_invalid_files_remain_undeletable(self) -> None:
        synced = next(item for item in self.backend.get_tasks() if item.metadata["key"] == "JPT-1")

        result = self.backend.delete_task_if_current(synced)

        self.assertFalse(result.ok)
        self.assertIn("only unsynced local drafts", result.message.lower())
        self.assertTrue(next(self.workspace.rglob("JPT-1.md")).exists())
        self.assertEqual([], list(layout.trash_dir(self.workspace).rglob("*.md")))
        self.assertEqual((0, 0, 0), self.provider_calls())

        path = self.backend._paths[synced.task_id]  # noqa: SLF001
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Synced card", "title: Local conflict"),
            encoding="utf-8",
        )
        state = SyncState.load(layout.state_file(self.workspace))
        state.mark_conflicts({"id-JPT-1"})
        state.save(layout.state_file(self.workspace))
        self.backend.reload_local()
        conflict = next(item for item in self.backend.get_tasks() if item.metadata["key"] == "JPT-1")

        self.assertEqual("conflict", conflict.metadata["sync_status"])
        self.assertFalse(self.backend.can_delete_task(conflict))
        self.assertFalse(self.backend.delete_task_if_current(conflict).ok)
        self.assertTrue(path.exists())
        self.assertEqual((0, 0, 0), self.provider_calls())

    def test_external_edit_after_selection_is_a_safe_race_failure(self) -> None:
        draft = self.make_draft()
        path = self.backend._paths[draft.task_id]  # noqa: SLF001
        path.write_text(path.read_text(encoding="utf-8") + "\nexternal edit\n", encoding="utf-8")

        result = self.backend.delete_task_if_current(draft)

        self.assertFalse(result.ok)
        self.assertIn("changed", result.message.lower())
        self.assertTrue(path.exists())
        self.assertEqual([], list(layout.trash_dir(self.workspace).rglob("*.md")))

    def test_change_during_validation_is_not_quarantined(self) -> None:
        from pykantui.workspace import markdown

        draft = self.make_draft()
        path = self.backend._paths[draft.task_id]  # noqa: SLF001
        real_read = markdown.read

        def racing_read(candidate: Path):  # type: ignore[no-untyped-def]
            parsed = real_read(candidate)
            candidate.write_text(
                candidate.read_text(encoding="utf-8") + "\nchanged during validation\n",
                encoding="utf-8",
            )
            return parsed

        with patch("pykantui.sync.provider.markdown.read", side_effect=racing_read):
            result = self.backend.delete_task_if_current(draft)

        self.assertFalse(result.ok)
        self.assertIn("changed", result.message.lower())
        self.assertTrue(path.exists())
        self.assertEqual([], list(layout.trash_dir(self.workspace).rglob("*.md")))
        self.assertEqual((0, 0, 0), self.provider_calls())

    def test_stale_row_identity_cannot_delete_a_different_draft_after_reload(self) -> None:
        first = self.make_draft("First draft")
        second = self.make_draft("Second draft")
        first_path = self.backend._paths[first.task_id]  # noqa: SLF001
        second_path = self.backend._paths[second.task_id]  # noqa: SLF001
        first_path.unlink()
        self.backend.reload_local()

        result = self.backend.delete_task_if_current(first)

        self.assertFalse(result.ok)
        self.assertIn("changed", result.message.lower())
        self.assertTrue(second_path.exists())
        self.assertIn(second.metadata["id"], {item.metadata["id"] for item in self.backend.get_tasks()})

    def test_tampered_path_outside_workspace_is_refused_and_left_untouched(self) -> None:
        from pykantui.sync.provider import _file_revision

        draft = self.make_draft()
        outside = self.workspace.parent / f"{self.workspace.name}-outside.md"
        outside.write_text("outside must survive", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)
        revision = _file_revision(outside)
        self.backend._paths[draft.task_id] = outside  # noqa: SLF001
        self.backend._source_revisions[draft.task_id] = revision  # noqa: SLF001
        forged = draft.model_copy(
            update={"metadata": {**draft.metadata, "_source_revision": revision}},
            deep=True,
        )

        result = self.backend.delete_task_if_current(forged)

        self.assertFalse(result.ok)
        self.assertIn("outside", result.message.lower())
        self.assertEqual("outside must survive", outside.read_text(encoding="utf-8"))
        self.assertEqual((0, 0, 0), self.provider_calls())


if __name__ == "__main__":
    unittest.main()
