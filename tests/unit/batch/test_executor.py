"""Exact-plan batch application, parent resolution, and safe resume."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path

from pykantui.batch.executor import apply_batch_plan
from pykantui.batch.journal import BatchApplyJournal
from pykantui.batch.models import BatchManifest
from pykantui.batch.planner import build_batch_plan
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import IssueDraft, IssueType, RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, ProviderSpec


class ApplyingProvider(Provider):
    spec = ProviderSpec(
        name="jira",
        label="Jira",
        capabilities=Capabilities(
            create_issues=True,
            move_issues=True,
            parent_issues=True,
            writable_fields=("title", "body", "column_id", "issue_type", "labels"),
        ),
    )

    def __init__(self) -> None:
        super().__init__({}, {})
        self.created_drafts: list[IssueDraft] = []
        self.moved: list[tuple[str, str]] = []
        self.remote: dict[str, RemoteIssue] = {}

    def list_projects(self) -> list[RemoteProject]:
        return []

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me", display_name="Me")

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        del project_id
        return [
            RemoteColumn(column_id="1", name="To Do"),
            RemoteColumn(column_id="2", name="In Progress"),
            RemoteColumn(column_id="3", name="Done"),
        ]

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        del project_id
        return [
            IssueType(type_id="10", name="Story"),
            IssueType(type_id="11", name="Sub-task", subtask=True, level=-1),
        ]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        del project_id
        return iter(self.remote.values())

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        del project_id
        self.created_drafts.append(draft)
        number = len(self.created_drafts)
        issue = RemoteIssue(
            issue_id=str(1000 + number),
            key=f"PAY-{number}",
            title=draft.title,
            body=draft.body,
            issue_type=draft.issue_type,
            column_id="1",
            status="To Do",
            parent_key=draft.parent_key,
        )
        self.remote[issue.key] = issue
        return issue

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self.moved.append((issue.key, column.column_id))
        self.remote[issue.key] = issue.model_copy(update={"column_id": column.column_id, "status": column.name})

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        del project_id
        return self.remote.get(issue.key)


def document() -> BatchManifest:
    return BatchManifest.model_validate(
        {
            "apiVersion": "pykantui.dev/v1alpha1",
            "kind": "IssueBatch",
            "metadata": {"name": "checkout"},
            "target": {"provider": "jira"},
            "defaults": {"type": "Story", "state": "To Do"},
            "issues": [
                {"ref": "child", "title": "Test it", "type": "Sub-task", "parent": "parent"},
                {
                    "ref": "parent",
                    "title": "Build it",
                    "state": {"name": "Done", "via": ["In Progress", "Done"]},
                },
            ],
        }
    )


class ExecutorTests(unittest.TestCase):
    def test_parent_is_created_first_and_child_receives_real_parent_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "issues.yml"
            source.write_text("stable", encoding="utf-8")
            provider = ApplyingProvider()
            project = RemoteProject(project_id="100", key="PAY", name="Payments")
            plan = build_batch_plan(document(), source, provider, project)

            report = apply_batch_plan(workspace, provider, project, plan)

            self.assertEqual([draft.title for draft in provider.created_drafts], ["Build it", "Test it"])
            self.assertEqual(provider.created_drafts[1].parent_key, "PAY-1")
            self.assertEqual(provider.moved, [("PAY-1", "2"), ("PAY-1", "3")])
            self.assertEqual(report.created, ["PAY-1", "PAY-2"])
            self.assertEqual(report.completed, ["parent", "child"])
            parent_card = next(path for path in workspace.rglob("PAY-1.md"))
            self.assertIn('batch-id="checkout" batch-ref="parent"', parent_card.read_text(encoding="utf-8"))

    def test_reapplying_completed_plan_does_not_duplicate_issues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "issues.yml"
            source.write_text("stable", encoding="utf-8")
            provider = ApplyingProvider()
            project = RemoteProject(project_id="100", key="PAY", name="Payments")
            plan = build_batch_plan(document(), source, provider, project)

            apply_batch_plan(workspace, provider, project, plan)
            second = apply_batch_plan(workspace, provider, project, plan)

        self.assertEqual(len(provider.created_drafts), 2)
        self.assertEqual(second.created, [])
        self.assertEqual(second.skipped, ["parent", "child"])

    def test_reapplying_completed_plan_repairs_missing_local_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "issues.yml"
            source.write_text("stable", encoding="utf-8")
            provider = ApplyingProvider()
            project = RemoteProject(project_id="100", key="PAY", name="Payments")
            plan = build_batch_plan(document(), source, provider, project)
            apply_batch_plan(workspace, provider, project, plan)
            parent = next(path for path in workspace.rglob("PAY-1.md"))
            parent.unlink()

            apply_batch_plan(workspace, provider, project, plan)

            self.assertTrue(any(path.name == "PAY-1.md" for path in workspace.rglob("*.md")))

    def test_an_in_flight_create_is_never_retried_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "issues.yml"
            source.write_text("stable", encoding="utf-8")
            provider = ApplyingProvider()
            project = RemoteProject(project_id="100", key="PAY", name="Payments")
            plan = build_batch_plan(document(), source, provider, project)
            journal_path = workspace / ".pykantui" / "batches" / "checkout.json"
            journal = BatchApplyJournal(batch_id="checkout", plan_hash=plan.plan_hash)
            journal.begin_create(journal_path, "parent", signature=plan.operations[0].signature())

            with self.assertRaisesRegex(ProviderError, "outcome is unknown"):
                apply_batch_plan(workspace, provider, project, plan)

        self.assertEqual(provider.created_drafts, [])

    def test_changed_source_invalidates_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "issues.yml"
            source.write_text("stable", encoding="utf-8")
            provider = ApplyingProvider()
            project = RemoteProject(project_id="100", key="PAY", name="Payments")
            plan = build_batch_plan(document(), source, provider, project)
            source.write_text("changed", encoding="utf-8")

            with self.assertRaisesRegex(ProviderError, "changed since planning"):
                apply_batch_plan(workspace, provider, project, plan)


if __name__ == "__main__":
    unittest.main()
