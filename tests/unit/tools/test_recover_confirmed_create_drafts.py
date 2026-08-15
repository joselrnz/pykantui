"""Fail-closed local recovery for confirmed creates whose drafts survived."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.recover_confirmed_create_drafts import RecoveryError, recover

from pykantui.tracker.models import CommentDraft, RemoteIssue
from pykantui.workspace import layout, markdown
from pykantui.workspace.state import SyncState

TAG = "PKT-E2E-20260814T122600Z-3bd16524"


class ConfirmedCreateRecoveryTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        workspace = root / "plane"
        draft = workspace / "plane/projects/JOSEP/todo/draft-card-01.md"
        canonical = workspace / "plane/projects/JOSEP/todo/JOSEP-8.md"
        draft.parent.mkdir(parents=True)
        title = f"[{TAG}:plane] card 01"
        draft_issue = RemoteIssue(
            issue_id="draft-card-01",
            title=title,
            column_id="todo",
            status="Todo",
            body="local body",
        )
        canonical_issue = draft_issue.model_copy(
            update={"issue_id": "remote-8", "key": "JOSEP-8", "url": "https://plane.test/8"}
        )
        draft.write_text(
            markdown.render(
                draft_issue,
                column_name="todo",
                provider="plane",
                notes="private note",
                comment_drafts=(
                    CommentDraft(
                        local_id="pending-1",
                        issue_id=draft_issue.issue_id,
                        body="pending comment",
                    ),
                ),
            ),
            encoding="utf-8",
        )
        canonical.write_text(
            markdown.render(canonical_issue, column_name="todo", provider="plane"),
            encoding="utf-8",
        )
        state = SyncState()
        state.remember(canonical_issue)
        state.save(layout.state_file(workspace))
        digest = hashlib.sha256(title.encode()).hexdigest()
        manifest = root / "before.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "run_tag": TAG,
                    "provider": "plane",
                    "capture": "read-only-before-local-repair",
                    "active_drafts": 1,
                    "canonical_cards": 1,
                    "exact_title_pairs": 1,
                    "pairs": [
                        {
                            "draft": {
                                "file": draft.relative_to(workspace).as_posix(),
                                "id": draft_issue.issue_id,
                                "title_sha256": digest,
                                "file_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
                            },
                            "canonical": {
                                "file": canonical.relative_to(workspace).as_posix(),
                                "id": canonical_issue.issue_id,
                                "title_sha256": digest,
                                "file_sha256": hashlib.sha256(canonical.read_bytes()).hexdigest(),
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return workspace, root / "quarantine", manifest, draft

    def test_dry_run_validates_without_changing_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace, quarantine, manifest, draft = self.fixture(Path(raw))

            result = recover(workspace, quarantine, manifest, run_tag=TAG, execute=False)

            self.assertEqual(1, result.validated_pairs)
            self.assertEqual(1, result.pending_comments)
            self.assertTrue(draft.is_file())
            self.assertFalse(quarantine.exists())

    def test_execute_merges_local_discussion_then_moves_draft_recoverably(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace, quarantine, manifest, draft = self.fixture(Path(raw))

            result = recover(workspace, quarantine, manifest, run_tag=TAG, execute=True)

            self.assertEqual(1, result.quarantined)
            self.assertFalse(draft.exists())
            moved = next(quarantine.rglob("draft-card-01.md"))
            self.assertTrue(moved.is_file())
            canonical = markdown.read(workspace / "plane/projects/JOSEP/todo/JOSEP-8.md")
            self.assertEqual("private note", canonical.notes)
            self.assertEqual(["pending-1"], [draft.local_id for draft in canonical.comment_drafts])
            self.assertEqual("remote-8", canonical.comment_drafts[0].issue_id)

    def test_hash_mismatch_fails_before_any_move(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace, quarantine, manifest, draft = self.fixture(Path(raw))
            draft.write_text(draft.read_text(encoding="utf-8") + "changed", encoding="utf-8")

            with self.assertRaisesRegex(RecoveryError, "hash"):
                recover(workspace, quarantine, manifest, run_tag=TAG, execute=True)

            self.assertTrue(draft.is_file())
            self.assertFalse(quarantine.exists())


if __name__ == "__main__":
    unittest.main()
