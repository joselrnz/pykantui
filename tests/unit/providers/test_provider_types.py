"""Provider item types stay human-readable, stable and request-bounded."""

from __future__ import annotations

import unittest
from collections import Counter

import httpx

from pykantui.core.work_items import WorkItemColumn
from pykantui.providers.clickup import ClickUpProvider
from pykantui.providers.clickup.client import ClickUpClient
from pykantui.providers.clickup.mapper import task_to_remote
from pykantui.providers.clickup.schemas import TaskWire as ClickUpTaskWire
from pykantui.providers.plane.mapper import work_item_to_remote
from pykantui.providers.plane.schemas import WorkItemWire as PlaneWorkItemWire
from pykantui.tracker.fields import CardFieldName
from pykantui.tracker.registry import specs


class ProviderTypeContractTests(unittest.TestCase):
    """Each provider declares only the type concept it really has."""

    def test_native_type_availability_matrix(self) -> None:
        availability = {
            spec.name: any(
                field.name is CardFieldName.ISSUE_TYPE
                and bool(field.provider_key)
                and field.available({})
                for field in spec.card_fields
            )
            for spec in specs()
        }

        self.assertEqual(
            {
                "asana": False,
                "clickup": True,
                "github": True,
                "jira": True,
                "linear": False,
                "monday": False,
                "plane": False,
                "shortcut": True,
                "trello": False,
            },
            availability,
        )

    def test_rows_and_split_type_column_uses_the_same_provider_matrix(self) -> None:
        availability = {
            spec.name: WorkItemColumn.TYPE in spec.available_table_fields({})
            for spec in specs()
        }

        self.assertEqual(
            {
                "asana": False,
                "clickup": True,
                "github": True,
                "jira": True,
                "linear": False,
                "monday": False,
                "plane": False,
                "shortcut": True,
                "trello": False,
            },
            availability,
        )

    def test_monday_type_is_available_only_when_its_board_column_is_configured(self) -> None:
        monday = next(spec for spec in specs() if spec.name == "monday")
        type_field = next(field for field in monday.card_fields if field.name is CardFieldName.ISSUE_TYPE)

        self.assertFalse(type_field.available({}))
        self.assertTrue(type_field.available({"type_column": "work_type"}))
        self.assertNotIn(WorkItemColumn.TYPE, monday.available_table_fields({}))
        self.assertIn(
            WorkItemColumn.TYPE,
            monday.available_table_fields({"type_column": "work_type"}),
        )

    def test_clickup_keeps_custom_type_id_and_maps_its_directory_name(self) -> None:
        issue = task_to_remote(
            ClickUpTaskWire(id="task-1", custom_item_id=1300, team_id="team-1"),
            {"1300": "Bug"},
        )

        self.assertEqual("Bug", issue.issue_type)
        self.assertEqual("1300", issue.extra["issue_type_id"])

    def test_clickup_builtin_and_unknown_types_are_safe_without_a_directory(self) -> None:
        ordinary = task_to_remote(ClickUpTaskWire(id="task-1", custom_item_id=0))
        malformed = task_to_remote(ClickUpTaskWire.model_validate({"id": "task-2", "custom_item_id": None}))

        self.assertEqual("Task", ordinary.issue_type)
        self.assertEqual("", malformed.issue_type)

    def test_plane_keeps_raw_type_id_without_claiming_a_display_type(self) -> None:
        issue = work_item_to_remote(
            PlaneWorkItemWire(id="work-1", type_id="type-1"),
            workspace="acme",
            project_id="project-1",
            identifier="ACME",
            states={},
            members={},
            labels=[],
        )

        self.assertEqual("", issue.issue_type)
        self.assertEqual("type-1", issue.extra["issue_type_id"])

        untyped = work_item_to_remote(
            PlaneWorkItemWire.model_validate({"id": "work-2", "type_id": None}),
            workspace="acme",
            project_id="project-1",
            identifier="ACME",
            states={},
            members={},
            labels=[],
        )
        self.assertEqual("", untyped.issue_type)


class ProviderTypeCallCountTests(unittest.TestCase):
    """Type-name resolution is one structural lookup, never one per item."""

    def test_clickup_fetches_one_type_directory_for_many_tasks(self) -> None:
        calls: Counter[str] = Counter()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/team/team-1/custom_item":
                calls["types"] += 1
                return httpx.Response(
                    200,
                    json={"custom_items": [{"id": 1300, "name": "Bug"}]},
                )
            if request.url.path == "/api/v2/list/list-1/task":
                calls["tasks"] += 1
                page = int(request.url.params["page"])
                size = 100 if page < 2 else 50
                return httpx.Response(
                    200,
                    json={
                        "tasks": [
                            {
                                "id": f"task-{page}-{index}",
                                "custom_item_id": 1300,
                                "team_id": "team-1",
                            }
                            for index in range(size)
                        ]
                    },
                )
            raise AssertionError(f"unexpected ClickUp request: {request.url}")

        provider = ClickUpProvider({"list_id": "list-1"}, {"token": "t"})
        raw_client = httpx.Client(
            base_url="https://api.clickup.com/api/v2",
            transport=httpx.MockTransport(handler),
        )
        provider._http = ClickUpClient(
            "https://api.clickup.com/api/v2",
            client=raw_client,
            retries=0,
        )
        self.addCleanup(provider.close)

        first = list(provider.iter_issues("list-1"))
        second = list(provider.iter_issues("list-1"))

        self.assertEqual(250, len(first))
        self.assertEqual("Bug", first[0].issue_type)
        self.assertEqual("Bug", second[-1].issue_type)
        self.assertEqual(1, calls["types"])
        self.assertEqual(6, calls["tasks"])


if __name__ == "__main__":
    unittest.main()
