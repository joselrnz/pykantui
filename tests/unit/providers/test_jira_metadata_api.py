"""Jira metadata and agile discovery endpoint contracts."""

from __future__ import annotations

import unittest

import httpx

from pykantui.api import TTL_STRUCTURE
from pykantui.api.errors import NotFoundError
from pykantui.providers.jira import JiraFieldType, JiraSprintState, routes
from pykantui.providers.jira.client import JiraApi, JiraClient
from pykantui.providers.jira.provider import JiraProvider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import IssueDraft, IssueType


class JiraMetadataRouteTests(unittest.TestCase):
    def test_closed_request_vocabularies_are_reusable_string_enums(self) -> None:
        self.assertIsInstance(JiraFieldType.CUSTOM, str)
        self.assertEqual(("custom", "system"), tuple(value.value for value in JiraFieldType))
        self.assertIsInstance(JiraSprintState.ACTIVE, str)
        self.assertEqual(
            ("active", "closed", "future"),
            tuple(value.value for value in JiraSprintState),
        )

    def test_routes_match_current_jira_cloud_endpoints(self) -> None:
        self.assertEqual(
            "/rest/api/3/issue/createmeta/JPT/issuetypes/10001",
            routes.create_field_metadata("JPT", "10001"),
        )
        self.assertEqual("/rest/api/3/issue/JPT-7/editmeta", routes.edit_metadata("JPT-7"))
        self.assertEqual("/rest/api/3/field/search", routes.FIELDS)
        self.assertEqual("/rest/api/3/priority/search", routes.PRIORITIES)
        self.assertEqual("/rest/api/3/label", routes.LABELS)
        self.assertEqual("/rest/agile/1.0/board/42/sprint", routes.board_sprints("42"))

    def test_path_parameters_cannot_change_the_route_structure(self) -> None:
        self.assertEqual(
            "/rest/api/3/issue/createmeta/A%2FB/issuetypes/1%2F2",
            routes.create_field_metadata("A/B", "1/2"),
        )
        self.assertEqual("/rest/api/3/issue/A%2FB/editmeta", routes.edit_metadata("A/B"))
        self.assertEqual("/rest/agile/1.0/board/1%2F2/sprint", routes.board_sprints("1/2"))


class JiraMetadataApiTests(unittest.TestCase):
    def _api(self, handler: httpx.MockTransport) -> JiraApi:
        client = JiraClient(
            "https://example.atlassian.net",
            client=httpx.Client(base_url="https://example.atlassian.net", transport=handler),
        )
        self.addCleanup(client.close)
        return JiraApi(client)

    def test_create_fields_supports_both_documented_collection_keys_and_pagination(self) -> None:
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            start = int(request.url.params.get("startAt", "0"))
            item = {
                "fieldId": "summary" if start == 0 else "customfield_10001",
                "key": "summary" if start == 0 else "customfield_10001",
                "name": "Summary" if start == 0 else "Customer",
                "operations": ["set"],
                "required": True,
                "schema": {"type": "string", "system": "summary" if start == 0 else None},
            }
            collection = "fields" if start == 0 else "results"
            return httpx.Response(
                200,
                json={"startAt": start, "maxResults": 1, "total": 2, collection: [item]},
            )

        fields = list(
            self._api(httpx.MockTransport(respond)).create_fields(
                "JPT", "10001", ttl=TTL_STRUCTURE
            )
        )

        self.assertEqual(["summary", "customfield_10001"], [field.fieldId for field in fields])
        self.assertEqual([0, 1], [int(request.url.params["startAt"]) for request in seen])
        self.assertTrue(all(int(request.url.params["maxResults"]) <= 200 for request in seen))

    def test_edit_metadata_parses_the_field_directory(self) -> None:
        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "fields": {
                        "summary": {
                            "key": "summary",
                            "name": "Summary",
                            "operations": ["set"],
                            "required": True,
                            "schema": {"type": "string", "system": "summary"},
                        }
                    }
                },
            )

        metadata = self._api(httpx.MockTransport(respond)).edit_metadata("JPT-7")

        self.assertEqual("Summary", metadata.fields["summary"].name)

    def test_fields_priorities_labels_and_sprints_are_fully_paginated(self) -> None:
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            start = int(request.url.params.get("startAt", "0"))
            common = {"startAt": start, "maxResults": 1, "total": 2, "isLast": start == 1}
            if request.url.path == routes.FIELDS:
                return httpx.Response(
                    200,
                    json={
                        **common,
                        "values": [
                            {
                                "id": f"customfield_{start}",
                                "key": f"customfield_{start}",
                                "name": f"Field {start}",
                                "schema": {"type": "string", "customId": start},
                            }
                        ],
                    },
                )
            if request.url.path == routes.PRIORITIES:
                return httpx.Response(
                    200,
                    json={**common, "values": [{"id": str(start), "name": f"Priority {start}"}]},
                )
            if request.url.path == routes.LABELS:
                return httpx.Response(200, json={**common, "values": [f"label-{start}"]})
            return httpx.Response(
                200,
                json={
                    **common,
                    "values": [
                        {"id": start, "name": f"Sprint {start}", "state": "active", "originBoardId": 42}
                    ],
                },
            )

        api = self._api(httpx.MockTransport(respond))

        fields = list(
            api.fields(
                ttl=TTL_STRUCTURE,
                field_types=(JiraFieldType.CUSTOM,),
                project_ids=("10000",),
                query="customer",
            )
        )
        priorities = list(api.priorities(ttl=TTL_STRUCTURE, project_ids=("10000",)))
        labels = list(api.labels(ttl=TTL_STRUCTURE))
        sprints = list(
            api.sprints(
                "42",
                ttl=TTL_STRUCTURE,
                states=(JiraSprintState.ACTIVE, JiraSprintState.FUTURE),
            )
        )

        self.assertEqual(["Field 0", "Field 1"], [field.name for field in fields])
        self.assertEqual(["Priority 0", "Priority 1"], [priority.name for priority in priorities])
        self.assertEqual(["label-0", "label-1"], labels)
        self.assertEqual(["Sprint 0", "Sprint 1"], [sprint.name for sprint in sprints])
        self.assertIs(type(sprints[0].state), str, "provider response values remain forward-compatible")
        field_requests = [request for request in seen if request.url.path == routes.FIELDS]
        self.assertEqual("custom", field_requests[0].url.params["type"])
        self.assertEqual("10000", field_requests[0].url.params["projectIds"])
        sprint_requests = [request for request in seen if request.url.path.endswith("/sprint")]
        self.assertEqual("active,future", sprint_requests[0].url.params["state"])

    def test_sprint_state_is_validated_before_a_request_is_sent(self) -> None:
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"isLast": True, "values": []})

        api = self._api(httpx.MockTransport(respond))

        with self.assertRaisesRegex(ValueError, "sprint state"):
            list(api.sprints("42", ttl=TTL_STRUCTURE, states=("running",)))
        self.assertEqual([], seen)

    def test_field_type_is_validated_before_a_request_is_sent(self) -> None:
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"isLast": True, "values": []})

        api = self._api(httpx.MockTransport(respond))

        with self.assertRaisesRegex(ValueError, "field type"):
            list(api.fields(ttl=TTL_STRUCTURE, field_types=("team",)))
        self.assertEqual([], seen)

    def test_existing_string_callers_remain_compatible(self) -> None:
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"isLast": True, "values": []})

        api = self._api(httpx.MockTransport(respond))

        list(api.fields(ttl=TTL_STRUCTURE, field_types=("system",)))
        list(api.sprints("42", ttl=TTL_STRUCTURE, states=("closed",)))

        self.assertEqual("system", seen[0].url.params["type"])
        self.assertEqual("closed", seen[1].url.params["state"])

    def test_non_string_label_is_rejected(self) -> None:
        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"isLast": True, "values": [42]})

        with self.assertRaisesRegex(ValueError, "non-string label"):
            list(self._api(httpx.MockTransport(respond)).labels(ttl=TTL_STRUCTURE))


class JiraCreateMetadataTests(unittest.TestCase):
    def _provider(self, fields: list[dict[str, object]] | None) -> JiraProvider:
        class FakeClient:
            def get(
                self,
                path: str,
                params: object = None,
                *,
                ttl: float = 0.0,
                label: str = "",
            ) -> object:
                del params, ttl, label
                if "/createmeta/" not in path:
                    raise AssertionError(f"unexpected request: {path}")
                if fields is None:
                    raise NotFoundError("create metadata unavailable")
                return {
                    "startAt": 0,
                    "maxResults": 200,
                    "total": len(fields),
                    "fields": fields,
                }

            def close(self) -> None:
                return None

        provider = JiraProvider({"base_url": "https://x"}, {"email": "e", "token": "t"})
        provider._http = FakeClient()  # type: ignore[assignment]
        provider.list_issue_types = lambda project_id: [  # type: ignore[method-assign]
            IssueType(type_id="10001", name="Task")
        ]
        return provider

    def test_required_custom_field_is_rejected_before_create(self) -> None:
        provider = self._provider(
            [
                {
                    "fieldId": "customfield_10042",
                    "key": "customfield_10042",
                    "name": "Customer impact",
                    "operations": ["set"],
                    "required": True,
                    "schema": {"type": "string", "customId": 10042},
                }
            ]
        )

        with self.assertRaisesRegex(ProviderError, "Customer impact"):
            provider.create_issue("10000", IssueDraft(title="Ship it", issue_type="Task"))

    def test_server_default_satisfies_a_required_custom_field(self) -> None:
        provider = self._provider(
            [
                {
                    "fieldId": "customfield_10042",
                    "key": "customfield_10042",
                    "name": "Customer impact",
                    "operations": ["set"],
                    "required": True,
                    "hasDefaultValue": True,
                    "defaultValue": "Internal",
                    "schema": {"type": "string", "customId": 10042},
                }
            ]
        )

        payload = provider.build_create_payload(
            "10000", IssueDraft(title="Ship it", issue_type="Task")
        )
        provider._validate_create_fields("10000", "10001", payload)

        self.assertEqual("Ship it", payload["summary"])

    def test_missing_granular_metadata_endpoint_preserves_create_compatibility(self) -> None:
        provider = self._provider(None)
        payload = provider.build_create_payload(
            "10000", IssueDraft(title="Ship it", issue_type="Task")
        )

        provider._validate_create_fields("10000", "10001", payload)

    def test_no_issue_type_id_needs_no_metadata_request(self) -> None:
        provider = self._provider(None)

        provider._validate_create_fields("10000", "", {"summary": "Ship it"})


if __name__ == "__main__":
    unittest.main()
