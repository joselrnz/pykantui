"""Provider-neutral contract for pulled and append-only provider comments.

These tests intentionally describe the public boundary before it exists.  A
provider maps its native comment/update/story shape into ``RemoteComment``;
the workspace hands ``CommentDraft`` back to ``create_comment``.  Nothing in
the sync layer should need Jira, GitHub, or Monday-specific field names.
"""

from __future__ import annotations

import unittest
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from pykantui.models import Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker import models
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import UnsupportedError
from pykantui.tracker.models import RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, ProviderSpec


def _comment_types() -> tuple[type[Any], type[Any]]:
    """Return the required models with one actionable failure per test."""
    remote = getattr(models, "RemoteComment", None)
    draft = getattr(models, "CommentDraft", None)
    if remote is None or draft is None:
        raise AssertionError(
            "tracker.models must define RemoteComment and CommentDraft before "
            "provider comment payloads are implemented"
        )
    return remote, draft


class BareProvider(Provider):
    """A provider inheriting the optional comment defaults."""

    spec = ProviderSpec(name="bare", label="Bare")

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me")

    def list_projects(self) -> list[RemoteProject]:
        return []

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return []

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(())


class CommentModelTests(unittest.TestCase):
    def test_remote_comment_keeps_opaque_ids_and_provider_metadata(self) -> None:
        RemoteComment, _ = _comment_types()

        comment = RemoteComment(
            comment_id="007",
            issue_id="10018",
            body="Review complete ✓",
            author="José",
            author_id="acct-9",
            created_at=datetime(2026, 8, 13, 12, 30, tzinfo=UTC),
            updated_at=datetime(2026, 8, 13, 12, 31, tzinfo=UTC),
            url="https://tracker.example/comments/007",
        )

        self.assertEqual("007", comment.comment_id)
        self.assertEqual("10018", comment.issue_id)
        self.assertEqual("Review complete ✓", comment.body)
        self.assertEqual("acct-9", comment.author_id)

    def test_comment_draft_has_a_stable_local_id_for_idempotency(self) -> None:
        _, CommentDraft = _comment_types()

        draft = CommentDraft(
            local_id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA",
            issue_id="10018",
            body="Please verify the release notes.",
        )

        self.assertEqual("comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA", draft.local_id)
        self.assertEqual("10018", draft.issue_id)

    def test_a_blank_comment_cannot_reach_a_provider(self) -> None:
        _, CommentDraft = _comment_types()

        with self.assertRaises(ValidationError):
            CommentDraft(
                local_id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA",
                issue_id="10018",
                body=" \n\t ",
            )


class CommentCapabilityTests(unittest.TestCase):
    def test_read_and_create_are_separate_capabilities(self) -> None:
        capabilities = Capabilities(read_comments=True, create_comments=False)

        self.assertTrue(capabilities.read_comments)
        self.assertFalse(capabilities.create_comments)

    def test_a_provider_without_comment_support_yields_no_remote_comments(self) -> None:
        issue = RemoteIssue(issue_id="10018", key="JPT-4")
        provider = BareProvider({}, {})

        self.assertEqual([], list(provider.iter_comments("JPT", issue)))

    def test_a_provider_without_create_support_fails_before_network_io(self) -> None:
        _, CommentDraft = _comment_types()
        issue = RemoteIssue(issue_id="10018", key="JPT-4")
        draft = CommentDraft(
            local_id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA",
            issue_id=issue.issue_id,
            body="Local draft",
        )

        with self.assertRaises(UnsupportedError):
            BareProvider({}, {}).create_comment("JPT", issue, draft)

    def test_a_non_provider_backend_has_a_stable_failed_refresh_contract(self) -> None:
        backend = JsonBackend()
        task = Task(task_id=1, title="Local", column_id=1)

        result = backend.refresh_task_comments(task)

        self.assertFalse(result.ok)
        self.assertIsNone(result.task)
        self.assertIn("no provider comments", result.message)


if __name__ == "__main__":
    unittest.main()
