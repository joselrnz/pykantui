"""Provider people fields stay human-readable without per-card lookups."""

from __future__ import annotations

import unittest
from collections import Counter
from typing import Any, cast

from pykantui.api import JsonHttp
from pykantui.core.work_items import WorkItemColumn
from pykantui.providers.asana.mapper import task_to_remote as asana_task
from pykantui.providers.asana.schemas import TaskWire as AsanaTaskWire
from pykantui.providers.clickup.mapper import task_to_remote as clickup_task
from pykantui.providers.clickup.schemas import TaskWire as ClickUpTaskWire
from pykantui.providers.github.mapper import issue_to_remote as github_issue
from pykantui.providers.github.schemas import IssueWire as GitHubIssueWire
from pykantui.providers.jira.mapper import issue_to_remote as jira_issue
from pykantui.providers.jira.schemas import IssueWire as JiraIssueWire
from pykantui.providers.linear.mapper import issue_to_remote as linear_issue
from pykantui.providers.linear.schemas import IssueWire as LinearIssueWire
from pykantui.providers.monday import operations as monday_operations
from pykantui.providers.monday.mapper import item_to_remote
from pykantui.providers.monday.schemas import ItemWire
from pykantui.providers.plane import PlaneProvider
from pykantui.providers.plane.mapper import work_item_to_remote
from pykantui.providers.plane.schemas import WorkItemWire
from pykantui.providers.shortcut.mapper import story_to_remote
from pykantui.providers.shortcut.schemas import StoryWire
from pykantui.providers.trello import TrelloProvider
from pykantui.providers.trello.mapper import card_to_remote
from pykantui.providers.trello.schemas import CardWire
from pykantui.tracker.registry import specs


class EmbeddedPeopleMappingTests(unittest.TestCase):
    """People already present in bulk responses need no follow-up request."""

    def test_all_nine_provider_mappers_keep_available_people_names(self) -> None:
        issues = {
            "asana": asana_task(
                AsanaTaskWire.model_validate(
                    {
                        "gid": "1",
                        "assignee": {"gid": "a", "name": "Alex"},
                        "created_by": {"gid": "r", "name": "Robin"},
                    }
                ),
                "project",
            ),
            "clickup": clickup_task(
                ClickUpTaskWire.model_validate(
                    {
                        "id": "1",
                        "assignees": [{"id": "a", "username": "Alex"}],
                        "creator": {"id": "r", "username": "Robin"},
                    }
                )
            ),
            "github": github_issue(
                GitHubIssueWire.model_validate(
                    {
                        "id": "1",
                        "assignees": [{"id": "a", "login": "Alex"}],
                        "user": {"id": "r", "login": "Robin"},
                    }
                ),
                "acme/repo",
                "status:",
                open_column="open",
                closed_column="closed",
            ),
            "jira": jira_issue(
                JiraIssueWire.model_validate(
                    {
                        "id": "1",
                        "fields": {
                            "status": {},
                            "assignee": {"accountId": "a", "displayName": "Alex"},
                            "reporter": {"accountId": "r", "displayName": "Robin"},
                        },
                    }
                ),
                "https://jira.example",
            ),
            "linear": linear_issue(
                LinearIssueWire.model_validate(
                    {
                        "id": "1",
                        "state": {},
                        "assignee": {"id": "a", "displayName": "Alex"},
                        "creator": {"id": "r", "displayName": "Robin"},
                    }
                )
            ),
            "monday": item_to_remote(
                ItemWire.model_validate(
                    {
                        "id": "1",
                        "group": {},
                        "creator": {"id": "r", "name": "Robin"},
                        "column_values": [
                            {"id": "people", "type": "people", "text": "Alex"}
                        ],
                    }
                ),
                "board",
                "",
                {},
                {"assignee": "people"},
            ),
            "plane": work_item_to_remote(
                WorkItemWire(id="1", assignees=["a"], created_by="r"),
                workspace="acme",
                project_id="project",
                identifier="ACME",
                states={},
                members={"a": "Alex", "r": "Robin"},
                labels=[],
            ),
            "shortcut": story_to_remote(
                StoryWire(id=1, owner_ids=["a"], requested_by_id="r"),
                {},
                {"a": "Alex", "r": "Robin"},
            ),
            "trello": card_to_remote(
                CardWire.model_validate(
                    {
                        "id": "1",
                        "idMembers": ["a"],
                        "actions": [{"type": "createCard", "idMemberCreator": "r"}],
                    }
                ),
                {},
                {"a": "Alex", "r": "Robin"},
            ),
        }

        self.assertEqual(
            {
                "asana",
                "clickup",
                "github",
                "jira",
                "linear",
                "monday",
                "plane",
                "shortcut",
                "trello",
            },
            set(issues),
        )
        for provider, issue in issues.items():
            with self.subTest(provider=provider):
                self.assertEqual("Alex", issue.assignee)
                self.assertEqual("Robin", issue.reporter)

    def test_all_nine_providers_offer_the_reporter_column(self) -> None:
        availability = {
            spec.name: WorkItemColumn.REPORTER in spec.available_table_fields({})
            for spec in specs()
        }

        self.assertEqual(
            {
                "asana": True,
                "clickup": True,
                "github": True,
                "jira": True,
                "linear": True,
                "monday": True,
                "plane": True,
                "shortcut": True,
                "trello": True,
            },
            availability,
        )

    def test_monday_maps_the_embedded_creator(self) -> None:
        item = ItemWire.model_validate(
            {
                "id": "1",
                "name": "Ship it",
                "group": {"id": "todo", "title": "To do"},
                "creator": {"id": "member-1", "name": "Alex Kim"},
            }
        )

        issue = item_to_remote(item, "board-1", "", {}, {})

        self.assertEqual("Alex Kim", issue.reporter)
        self.assertEqual("member-1", issue.reporter_id)

    def test_monday_bulk_and_single_queries_request_creator_together_with_item(self) -> None:
        for query in (monday_operations.ITEMS_QUERY, monday_operations.ONE_ITEM_QUERY):
            with self.subTest(query=query.splitlines()[1].strip()):
                self.assertIn("creator { id name }", query)

    def test_plane_resolves_assignees_and_reporter_from_one_directory(self) -> None:
        issue = work_item_to_remote(
            WorkItemWire(
                id="work-1",
                name="Ship it",
                state="todo",
                assignees=["member-1", "departed-member"],
                created_by="member-2",
            ),
            workspace="acme",
            project_id="project-1",
            identifier="ACME",
            states={"todo": "To do"},
            members={"member-1": "Alex Kim", "member-2": "Sam Lee"},
            labels=[],
        )

        self.assertEqual("Alex Kim", issue.assignee)
        self.assertEqual(("member-1", "departed-member"), issue.assignee_ids)
        self.assertEqual("Sam Lee", issue.reporter)
        self.assertEqual("member-2", issue.reporter_id)

    def test_shortcut_resolves_owner_and_requester_from_one_directory(self) -> None:
        issue = story_to_remote(
            StoryWire(
                id=1,
                name="Ship it",
                workflow_state_id=10,
                owner_ids=["member-1", "unknown"],
                requested_by_id="member-2",
            ),
            {"10": "To do"},
            {"member-1": "Alex Kim", "member-2": "Sam Lee"},
        )

        self.assertEqual("Alex Kim", issue.assignee)
        self.assertEqual(("member-1", "unknown"), issue.assignee_ids)
        self.assertEqual("Sam Lee", issue.reporter)
        self.assertEqual("member-2", issue.reporter_id)

    def test_trello_mapper_handles_unknown_and_malformed_member_references(self) -> None:
        issue = card_to_remote(
            CardWire.model_validate(
                {
                    "id": "card-1",
                    "name": "Ship it",
                    "idList": "todo",
                    "idMembers": ["member-1", "unknown"],
                    "actions": [
                        {
                            "idMemberCreator": "departed",
                            "memberCreator": {"id": "departed", "fullName": "Former User"},
                        },
                        None,
                    ],
                }
            ),
            {"todo": "To do"},
            {"member-1": "Alex Kim"},
        )

        self.assertEqual("Alex Kim", issue.assignee)
        self.assertEqual(("member-1", "unknown"), issue.assignee_ids)
        self.assertEqual("Former User", issue.reporter)
        self.assertEqual("departed", issue.reporter_id)


class ProviderPeopleCallCountTests(unittest.TestCase):
    """Directory reads are bounded by project/board, never card count."""

    def test_plane_fetches_each_project_member_directory_once(self) -> None:
        calls: Counter[str] = Counter()

        class FakeHttp:
            def get(self, path: str, params: Any = None, **_: Any) -> Any:
                if path.endswith("/projects/project-1/"):
                    return {"id": "project-1", "identifier": "ONE"}
                if path.endswith("/projects/project-2/"):
                    return {"id": "project-2", "identifier": "TWO"}
                if path.endswith("/states/"):
                    return {
                        "results": [{"id": "todo", "name": "To do", "group": "unstarted"}],
                        "next_page_results": False,
                    }
                if path.endswith("/members/"):
                    project = "project-1" if "project-1" in path else "project-2"
                    calls[f"members:{project}"] += 1
                    return [{"id": f"user-{project}", "display_name": f"User {project}"}]
                if path.endswith("/work-items/"):
                    project = "project-1" if "project-1" in path else "project-2"
                    return {
                        "results": [
                            {
                                "id": f"item-{index}",
                                "name": f"Item {index}",
                                "state": "todo",
                                "assignees": [f"user-{project}"],
                                "created_by": f"user-{project}",
                            }
                            for index in range(100)
                        ],
                        "next_page_results": False,
                    }
                raise AssertionError(f"unexpected Plane request: {path}")

        provider = PlaneProvider({"workspace": "acme", "project_id": "project-1"}, {"token": "t"})
        provider._http = cast(JsonHttp, FakeHttp())

        first = list(provider.iter_issues("project-1"))
        again = list(provider.iter_issues("project-1"))
        second = list(provider.iter_issues("project-2"))

        self.assertEqual(100, len(first))
        self.assertEqual("User project-1", first[0].assignee)
        self.assertEqual("User project-1", again[0].reporter)
        self.assertEqual("User project-2", second[0].reporter)
        self.assertEqual(Counter({"members:project-1": 1, "members:project-2": 1}), calls)

    def test_trello_fetches_one_member_directory_for_many_cards(self) -> None:
        calls: Counter[str] = Counter()
        seen_card_params: list[dict[str, Any]] = []

        class FakeHttp:
            def get(self, path: str, params: Any = None, **_: Any) -> Any:
                if path == "/boards/board-1/lists":
                    return [{"id": "todo", "name": "To do", "pos": 1}]
                if path == "/boards/board-1/members":
                    calls["members"] += 1
                    return [
                        {"id": "member-1", "fullName": "Alex Kim", "username": "alex"},
                        {"id": "member-2", "fullName": "Sam Lee", "username": "sam"},
                    ]
                if path == "/boards/board-1/cards":
                    calls["cards"] += 1
                    seen_card_params.append(dict(params))
                    return [
                        {
                            "id": f"card-{index}",
                            "name": f"Card {index}",
                            "idList": "todo",
                            "idMembers": ["member-1"],
                            "actions": [
                                {
                                    "type": "createCard",
                                    "idMemberCreator": "member-2",
                                    "memberCreator": {
                                        "id": "member-2",
                                        "fullName": "Sam Lee",
                                        "username": "sam",
                                    },
                                }
                            ],
                        }
                        for index in range(250)
                    ]
                raise AssertionError(f"unexpected Trello request: {path}")

        provider = TrelloProvider({"board_id": "board-1"}, {"key": "k", "token": "t"})
        provider._http = cast(JsonHttp, FakeHttp())

        issues = list(provider.iter_issues("board-1"))
        issues_again = list(provider.iter_issues("board-1"))

        self.assertEqual(250, len(issues))
        self.assertEqual("Alex Kim", issues[0].assignee)
        self.assertEqual("Sam Lee", issues[0].reporter)
        self.assertEqual("Alex Kim", issues_again[-1].assignee)
        self.assertEqual(1, calls["members"])
        self.assertEqual(2, calls["cards"])
        self.assertTrue(all(params["actions"] == "createCard,copyCard" for params in seen_card_params))


if __name__ == "__main__":
    unittest.main()
