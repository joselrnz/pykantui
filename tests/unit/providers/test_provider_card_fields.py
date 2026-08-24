"""Provider-owned card schemas keep UI fields and API payloads aligned."""

from __future__ import annotations

import unittest
from datetime import date

from pykantui.providers.asana import AsanaProvider
from pykantui.providers.clickup import ClickUpProvider
from pykantui.providers.forgejo import ForgejoProvider
from pykantui.providers.github import GitHubProvider
from pykantui.providers.github.payloads import update_issue_payload as github_update_payload
from pykantui.providers.jira import JiraProvider
from pykantui.providers.linear import LinearProvider
from pykantui.providers.monday import MondayProvider
from pykantui.providers.plane import PlaneProvider
from pykantui.providers.shortcut import ShortcutProvider
from pykantui.providers.trello import TrelloProvider
from pykantui.tracker import UnsupportedError, specs
from pykantui.tracker.fields import CARD_FIELD_ORDER, CardFieldKind, CardFieldName
from pykantui.tracker.filter_fields import FilterFieldName
from pykantui.tracker.models import IssueDraft, IssueEdit, IssueType, RemoteIssue

EXPECTED_EDITABLE = {
    "asana": {"title", "body", "column_id", "assignee", "due_date"},
    "clickup": {"title", "body", "column_id", "assignee", "issue_type", "priority", "labels", "due_date"},
    "forgejo": {"title", "body", "column_id", "assignee", "labels", "due_date"},
    "github": {"title", "body", "column_id", "assignee", "issue_type", "labels"},
    "jira": {
        "title",
        "body",
        "column_id",
        "assignee",
        "issue_type",
        "priority",
        "labels",
        "components",
        "due_date",
    },
    "linear": {"title", "body", "column_id", "assignee", "priority", "labels", "due_date"},
    "monday": {"title", "body", "column_id", "assignee", "issue_type", "priority", "labels", "due_date"},
    "plane": {"title", "body", "column_id", "assignee", "priority", "labels", "due_date"},
    "shortcut": {"title", "body", "column_id", "assignee", "issue_type", "labels", "due_date"},
    "trello": {"title", "body", "column_id", "assignee", "labels", "due_date"},
}

EXPECTED_FILTERS = {
    "asana": {
        "scope": "Project",
        "status": "Section",
        "assignee": "Assignee",
        "key": "Task GID",
    },
    "clickup": {
        "scope": "List",
        "status": "Status",
        "assignee": "Assignee",
        "issue_type": "Type",
        "priority": "Priority",
        "labels": "Tags",
        "key": "Task ID",
    },
    "forgejo": {
        "scope": "Repository",
        "status": "State",
        "assignee": "Assignee",
        "labels": "Labels",
        "key": "Issue Number",
    },
    "github": {
        "scope": "Repository",
        "status": "State",
        "assignee": "Assignee",
        "issue_type": "Issue Type",
        "labels": "Labels",
        "key": "Issue Number",
    },
    "jira": {
        "scope": "Project",
        "status": "Status",
        "assignee": "Assignee",
        "issue_type": "Type",
        "priority": "Priority",
        "labels": "Labels",
        "key": "Issue Key",
        "sprint": "Active Sprint",
        "query": "JQL Query",
    },
    "linear": {
        "scope": "Team",
        "status": "Status",
        "assignee": "Assignee",
        "priority": "Priority",
        "labels": "Labels",
        "key": "Issue ID",
    },
    # Optional Monday boxes are added only when their board-column ids exist.
    "monday": {"scope": "Board", "status": "Status", "key": "Item ID"},
    "plane": {
        "scope": "Project",
        "status": "State",
        "assignee": "Assignee",
        "priority": "Priority",
        "labels": "Labels",
        "key": "Work Item ID",
    },
    "shortcut": {
        "scope": "Workflow",
        "status": "Workflow State",
        "assignee": "Owner",
        "issue_type": "Story Type",
        "labels": "Labels",
        "key": "Story ID",
    },
    "trello": {
        "scope": "Board",
        "status": "List",
        "assignee": "Member",
        "labels": "Labels",
        "key": "Card ID",
    },
}


class ProviderCardFieldSchemaTests(unittest.TestCase):
    def test_every_provider_declares_every_normalised_field_once(self) -> None:
        for spec in specs():
            with self.subTest(provider=spec.name):
                self.assertEqual(CARD_FIELD_ORDER, tuple(field.name for field in spec.card_fields))

    def test_each_field_has_the_correct_value_kind(self) -> None:
        expected = {
            CardFieldName.TITLE: CardFieldKind.TEXT,
            CardFieldName.BODY: CardFieldKind.MARKDOWN,
            CardFieldName.COLUMN: CardFieldKind.COLUMN,
            CardFieldName.ASSIGNEE: CardFieldKind.USER,
            CardFieldName.ISSUE_TYPE: CardFieldKind.ISSUE_TYPE,
            CardFieldName.PRIORITY: CardFieldKind.PRIORITY,
            CardFieldName.LABELS: CardFieldKind.LABELS,
            CardFieldName.COMPONENTS: CardFieldKind.COMPONENTS,
            CardFieldName.DUE_DATE: CardFieldKind.DATE,
        }
        for spec in specs():
            for field in spec.card_fields:
                with self.subTest(provider=spec.name, field=field.name):
                    self.assertIs(expected[field.name], field.kind)

    def test_provider_edit_sets_match_their_apis(self) -> None:
        for spec in specs():
            with self.subTest(provider=spec.name):
                self.assertEqual(EXPECTED_EDITABLE[spec.name], set(spec.editable_card_fields()))

    def test_create_and_edit_are_declared_separately(self) -> None:
        jira = next(spec for spec in specs() if spec.name == "jira")
        title = jira.card_field(CardFieldName.TITLE)
        issue_type = jira.card_field(CardFieldName.ISSUE_TYPE)

        self.assertTrue(title.editable)
        self.assertTrue(title.creatable)
        self.assertTrue(title.required_on_create)
        self.assertTrue(issue_type.required_on_create)

    def test_monday_board_fields_name_the_required_column_mapping(self) -> None:
        monday = next(spec for spec in specs() if spec.name == "monday")

        self.assertEqual("description_column", monday.card_field(CardFieldName.BODY).configuration_key)
        self.assertEqual("assignee_column", monday.card_field(CardFieldName.ASSIGNEE).configuration_key)

    def test_supported_fields_name_the_provider_native_api_field(self) -> None:
        for spec in specs():
            for field in spec.card_fields:
                if field.editable or field.creatable:
                    with self.subTest(provider=spec.name, field=field.name):
                        self.assertTrue(field.provider_key)


class ProviderFilterFieldSchemaTests(unittest.TestCase):
    def test_each_provider_exposes_only_its_filter_boxes(self) -> None:
        for spec in specs():
            with self.subTest(provider=spec.name):
                actual = {field.name.value: field.label for field in spec.filter_fields()}
                self.assertEqual(EXPECTED_FILTERS[spec.name], actual)

    def test_monday_optional_boxes_require_column_mappings(self) -> None:
        monday = next(spec for spec in specs() if spec.name == "monday")
        configured = monday.filter_fields(
            {
                "assignee_column": "people",
                "type_column": "type",
                "priority_column": "priority",
                "labels_column": "labels",
            }
        )

        self.assertEqual(
            {
                FilterFieldName.SCOPE,
                FilterFieldName.STATUS,
                FilterFieldName.ASSIGNEE,
                FilterFieldName.ISSUE_TYPE,
                FilterFieldName.PRIORITY,
                FilterFieldName.LABELS,
                FilterFieldName.KEY,
            },
            {field.name for field in configured},
        )

    def test_only_jira_declares_a_provider_query_box(self) -> None:
        query_providers = {
            spec.name for spec in specs() if any(field.name is FilterFieldName.QUERY for field in spec.filter_fields())
        }

        self.assertEqual({"jira"}, query_providers)


class ProviderCreatePayloadTests(unittest.TestCase):
    def draft(self) -> IssueDraft:
        return IssueDraft(
            title="Ship it",
            body="Details",
            column_id="state-1",
            assignee="Alex",
            assignee_ids=("user-1",),
            issue_type="Bug",
            priority="High",
            labels=("backend",),
            due_date=date(2026, 8, 20),
            parent_key="P-1",
        )

    def test_asana_create_payload(self) -> None:
        provider = AsanaProvider({"project_id": "p"}, {"token": "t"})
        self.assertEqual(
            {"name": "Ship it", "notes": "Details", "projects": ["p"], "assignee": "user-1", "due_on": "2026-08-20"},
            provider.build_create_payload("p", self.draft()),
        )

    def test_clickup_create_payload(self) -> None:
        provider = ClickUpProvider({"list_id": "l"}, {"token": "t"})
        payload = provider.build_create_payload("l", self.draft())
        self.assertEqual("state-1", payload["status"])
        self.assertEqual(["user-1"], payload["assignees"])
        self.assertEqual(["backend"], payload["tags"])
        self.assertEqual(2, payload["priority"])

    def test_github_create_payload(self) -> None:
        provider = GitHubProvider({"repo": "a/b"}, {"token": "t"})
        provider.list_issue_types = lambda project_id: [  # type: ignore[method-assign]
            IssueType(type_id="2", name="Bug")
        ]
        payload = provider.build_create_payload("a/b", self.draft())
        self.assertEqual(["user-1"], payload["assignees"])
        self.assertEqual("Bug", payload["type"])
        self.assertEqual(["backend", "state-1"], payload["labels"])

    def test_forgejo_create_payload_resolves_label_ids(self) -> None:
        provider = ForgejoProvider(
            {"base_url": "https://forgejo.test", "repo": "a/b"},
            {"token": "t"},
        )
        provider._resolve_label_ids = lambda project_id, names: [10, 11]  # type: ignore[method-assign]
        payload = provider.build_create_payload("a/b", self.draft())
        self.assertEqual(["user-1"], payload["assignees"])
        self.assertEqual([10, 11], payload["labels"])
        self.assertEqual("2026-08-20T00:00:00Z", payload["due_date"])

    def test_github_rejects_a_type_when_the_repository_has_none(self) -> None:
        provider = GitHubProvider({"repo": "a/b"}, {"token": "t"})
        provider.list_issue_types = lambda project_id: []  # type: ignore[method-assign]

        with self.assertRaises(UnsupportedError):
            provider.build_create_payload("a/b", self.draft())

    def test_jira_create_payload_includes_priority(self) -> None:
        provider = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        provider.resolve_issue_type = lambda project_id, wanted: IssueType(type_id="10", name="Bug")  # type: ignore[method-assign]
        payload = provider.build_create_payload("100", self.draft())
        self.assertEqual({"name": "High"}, payload["priority"])
        self.assertEqual({"accountId": "user-1"}, payload["assignee"])

    def test_linear_create_payload(self) -> None:
        provider = LinearProvider({"team_id": "team"}, {"token": "t"})
        provider._resolve_label_ids = lambda values: ["label-1"]  # type: ignore[method-assign]
        payload = provider.build_create_payload("team", self.draft())
        self.assertEqual("team", payload["teamId"])
        self.assertEqual(["label-1"], payload["labelIds"])
        self.assertEqual(2, payload["priority"])

    def test_plane_create_payload(self) -> None:
        provider = PlaneProvider({"workspace": "w", "project_id": "p"}, {"token": "t"})
        provider._resolve_label_ids = lambda values: ["label-1"]  # type: ignore[method-assign]
        payload = provider.build_create_payload("p", self.draft())
        self.assertEqual(["label-1"], payload["labels"])
        self.assertNotIn("type", payload)
        self.assertNotIn("type_id", payload)

    def test_monday_create_payload_uses_only_configured_columns(self) -> None:
        provider = MondayProvider(
            {"board_id": "b", "status_column": "status", "assignee_column": "people", "priority_column": "priority"},
            {"token": "t"},
        )
        payload = provider.build_create_payload("b", self.draft())
        values = __import__("json").loads(payload["values"])
        self.assertEqual({"index": "state-1"}, values["status"])
        self.assertEqual({"personsAndTeams": [{"id": "user-1", "kind": "person"}]}, values["people"])
        self.assertEqual({"label": "High"}, values["priority"])
        self.assertNotIn("backend", values)

    def test_shortcut_create_payload(self) -> None:
        provider = ShortcutProvider({"workflow_id": "1"}, {"token": "t"})
        payload = provider.build_create_payload("1", self.draft())
        self.assertEqual(["user-1"], payload["owner_ids"])
        self.assertEqual("bug", payload["story_type"])

    def test_trello_create_payload(self) -> None:
        provider = TrelloProvider({"board_id": "b"}, {"key": "k", "token": "t"})
        provider._resolve_label_ids = lambda values: ["label-1"]  # type: ignore[method-assign]
        payload = provider.build_create_payload("b", self.draft())
        self.assertEqual("user-1", payload["idMembers"])
        self.assertEqual("label-1", payload["idLabels"])


class ProviderUpdatePayloadEdgeTests(unittest.TestCase):
    def test_github_explicit_label_clear_does_not_leave_remote_labels_behind(self) -> None:
        issue = RemoteIssue(
            issue_id="1",
            key="1",
            title="Example",
            column_id="open",
            labels=("backend", "bug"),
        )

        payload = github_update_payload(
            issue,
            IssueEdit(cleared=("labels",)),
            prefix="status:",
            resolved_type=None,
            open_column="open",
            closed_column="closed",
        )

        self.assertEqual([], payload["labels"])


if __name__ == "__main__":
    unittest.main()
