"""Provider-aware batch planning without remote writes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pykantui.batch.models import BatchManifest
from pykantui.batch.planner import BatchPlan, build_batch_plan
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import IssueType, RemoteColumn, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, ProviderSpec


class FakeProvider(Provider):
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

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        del project_id
        return [
            RemoteColumn(column_id="1", name="To Do", status_ids=("1",)),
            RemoteColumn(column_id="2", name="In Progress", status_ids=("2",)),
            RemoteColumn(column_id="3", name="Done", status_ids=("3",)),
        ]

    def list_projects(self) -> list[RemoteProject]:
        return []

    def iter_issues(self, project_id: str):  # type: ignore[no-untyped-def]
        del project_id
        return iter(())

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me", display_name="Me")

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        del project_id
        return [
            IssueType(type_id="10", name="Story"),
            IssueType(type_id="11", name="Sub-task", subtask=True, level=-1),
        ]


def manifest(*issues: dict[str, object]) -> BatchManifest:
    return BatchManifest.model_validate(
        {
            "apiVersion": "pykantui.dev/v1alpha1",
            "kind": "IssueBatch",
            "metadata": {"name": "checkout"},
            "target": {"provider": "jira"},
            "defaults": {"type": "Story", "state": "To Do"},
            "issues": list(issues),
        }
    )


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FakeProvider({}, {})
        self.project = RemoteProject(project_id="100", key="PAY", name="Payments")

    def plan(self, document: BatchManifest) -> BatchPlan:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "issues.yml"
            source.write_text("stable", encoding="utf-8")
            return build_batch_plan(document, source, self.provider, self.project)

    def test_plan_resolves_parent_before_subtask_and_explicit_transition_path(self) -> None:
        plan = self.plan(
            manifest(
                {"ref": "child", "title": "Test it", "type": "Sub-task", "parent": "parent"},
                {
                    "ref": "parent",
                    "title": "Build it",
                    "state": {"name": "Done", "via": ["In Progress", "Done"]},
                },
            )
        )

        self.assertEqual([item.ref for item in plan.operations], ["parent", "child"])
        self.assertEqual([hop.name for hop in plan.operations[0].transitions], ["In Progress", "Done"])
        self.assertEqual(plan.operations[1].parent_ref, "parent")
        self.assertTrue(plan.verify_hash())

    def test_missing_titles_block_plan_before_provider_writes(self) -> None:
        with self.assertRaisesRegex(ProviderError, "needs a title"):
            self.plan(manifest({"ref": "one", "title": None}))

    def test_subtask_without_parent_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProviderError, "requires parent"):
            self.plan(manifest({"ref": "one", "title": "Child", "type": "Sub-task"}))

    def test_non_subtask_with_parent_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProviderError, "not a sub-task"):
            self.plan(
                manifest(
                    {"ref": "parent", "title": "Parent"},
                    {"ref": "child", "title": "Wrong", "type": "Story", "parent": "parent"},
                )
            )

    def test_unknown_state_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProviderError, "no state matching"):
            self.plan(manifest({"ref": "one", "title": "One", "state": "Imaginary"}))

    def test_explicit_default_state_is_still_verified_after_creation(self) -> None:
        plan = self.plan(manifest({"ref": "one", "title": "One", "state": "To Do"}))

        self.assertEqual([hop.name for hop in plan.operations[0].transitions], ["To Do"])

    def test_provider_without_parent_capability_rejects_hierarchy(self) -> None:
        class NoParentProvider(FakeProvider):
            spec = FakeProvider.spec.model_copy(
                update={
                    "capabilities": Capabilities(
                        create_issues=True,
                        move_issues=True,
                        writable_fields=("title", "body", "column_id", "issue_type", "labels"),
                    )
                }
            )

        self.provider = NoParentProvider({}, {})
        with self.assertRaisesRegex(ProviderError, "does not support parent"):
            self.plan(
                manifest(
                    {"ref": "parent", "title": "Parent"},
                    {"ref": "child", "title": "Child", "type": "Sub-task", "parent": "parent"},
                )
            )

    def test_tampering_invalidates_plan_hash(self) -> None:
        plan = self.plan(manifest({"ref": "one", "title": "One"}))
        changed = plan.model_copy(
            update={"operations": (plan.operations[0].model_copy(update={"title": "Changed"}),)}
        )

        self.assertFalse(changed.verify_hash())


if __name__ == "__main__":
    unittest.main()
