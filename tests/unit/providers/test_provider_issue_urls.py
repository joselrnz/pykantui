"""Canonical issue-link origins at provider boundaries."""

from __future__ import annotations

import json
import unittest

import httpx

from pykantui.providers.monday.client import MondayApi, MondayClient
from pykantui.providers.monday.mapper import board_to_remote, item_to_remote
from pykantui.providers.monday.schemas import BoardSummaryWire, ItemWire
from pykantui.providers.plane.client import PlaneClient
from pykantui.providers.plane.mapper import project_to_remote, work_item_to_remote
from pykantui.providers.plane.provider import PlaneProvider
from pykantui.providers.plane.schemas import ProjectWire, WorkItemWire


class PlaneIssueUrlTests(unittest.TestCase):
    def test_cloud_api_origin_maps_to_plane_cloud_web_origin(self) -> None:
        issue = work_item_to_remote(
            WorkItemWire(id="work-1"),
            workspace="acme",
            project_id="project-1",
            identifier="ACME",
            states={},
            members={},
            labels=[],
            api_base_url="https://api.plane.so",
        )

        self.assertEqual(
            "https://app.plane.so/acme/projects/project-1/issues/work-1",
            issue.url,
        )

    def test_self_hosted_api_origin_is_preserved_for_project_and_issue(self) -> None:
        project = project_to_remote(
            ProjectWire(id="project-1", identifier="ACME", name="Acme"),
            "acme",
            api_base_url="https://plane.acme.test/api-proxy",
        )
        issue = work_item_to_remote(
            WorkItemWire(id="work-1"),
            workspace="acme",
            project_id="project-1",
            identifier="ACME",
            states={},
            members={},
            labels=[],
            api_base_url="https://plane.acme.test/api-proxy",
        )

        self.assertEqual(
            "https://plane.acme.test/acme/projects/project-1/issues/",
            project.url,
        )
        self.assertEqual(
            "https://plane.acme.test/acme/projects/project-1/issues/work-1",
            issue.url,
        )

    def test_configured_self_hosted_origin_flows_through_provider_mapping(self) -> None:
        requested: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "results": [{"id": "project-1", "identifier": "ACME", "name": "Acme"}],
                    "next_page_results": False,
                },
            )

        raw_client = httpx.Client(
            base_url="https://plane.acme.test",
            transport=httpx.MockTransport(respond),
        )
        provider = PlaneProvider(
            {"workspace": "acme", "base_url": "https://plane.acme.test"},
            {"token": "test-token"},
        )
        provider._http = PlaneClient("https://plane.acme.test", client=raw_client)
        try:
            projects = provider.list_projects()
        finally:
            provider.close()

        self.assertEqual(
            ["https://plane.acme.test/acme/projects/project-1/issues/"],
            [project.url for project in projects],
        )
        self.assertEqual(1, len(requested))
        self.assertIn("/api/v1/workspaces/acme/projects/", requested[0])


class MondayIssueUrlTests(unittest.TestCase):
    def test_mapper_prefers_api_returned_canonical_board_and_item_urls(self) -> None:
        board = board_to_remote(
            BoardSummaryWire(
                id="board-1",
                name="Roadmap",
                url="https://acme.monday.com/boards/board-1",
            )
        )
        item = item_to_remote(
            ItemWire.model_validate(
                {
                    "id": "item-1",
                    "name": "Ship it",
                    "url": "https://acme.monday.com/boards/board-1/pulses/item-1",
                    "group": {"id": "todo", "title": "To do"},
                }
            ),
            "board-1",
            "",
            {},
            {},
        )

        self.assertEqual("https://acme.monday.com/boards/board-1", board.url)
        self.assertEqual(
            "https://acme.monday.com/boards/board-1/pulses/item-1",
            item.url,
        )

    def test_mock_transport_queries_and_parses_canonical_item_url(self) -> None:
        queries: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            query = str(body["query"])
            queries.append(query)
            data: dict[str, object]
            if "boards (limit:" in query:
                data = {
                    "boards": [
                        {
                            "id": "board-1",
                            "name": "Roadmap",
                            "url": "https://acme.monday.com/boards/board-1",
                        }
                    ]
                }
            elif "items_page" in query:
                data = {
                    "boards": [
                        {
                            "items_page": {
                                "cursor": None,
                                "items": [
                                    {
                                        "id": "item-1",
                                        "name": "Ship it",
                                        "url": "https://acme.monday.com/boards/board-1/pulses/item-1",
                                        "group": {"id": "todo", "title": "To do"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            else:
                data = {
                    "items": [
                        {
                            "id": "item-1",
                            "name": "Ship it",
                            "url": "https://acme.monday.com/boards/board-1/pulses/item-1",
                            "group": {"id": "todo", "title": "To do"},
                        }
                    ]
                }
            return httpx.Response(200, json={"data": data})

        raw_client = httpx.Client(
            base_url="https://api.monday.test/v2",
            transport=httpx.MockTransport(respond),
        )
        transport = MondayClient("https://api.monday.test/v2", client=raw_client)
        api = MondayApi(transport)
        try:
            boards = list(api.boards())
            listed = list(api.items("board-1"))
            single = api.item("item-1")
        finally:
            transport.close()

        self.assertEqual(3, len(queries))
        self.assertTrue(all("url" in query for query in queries))
        self.assertEqual("https://acme.monday.com/boards/board-1", boards[0].url)
        self.assertEqual(
            "https://acme.monday.com/boards/board-1/pulses/item-1",
            listed[0].url,
        )
        self.assertIsNotNone(single)
        self.assertEqual(listed[0].url, single.url)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
