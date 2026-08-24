"""Forgejo's REST adapter follows the shared provider contract."""

from __future__ import annotations

import unittest
from datetime import date

import httpx

from pykantui.providers.forgejo import ForgejoProvider, routes
from pykantui.providers.forgejo.client import ForgejoClient
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import CommentDraft, IssueDraft, IssueEdit, RemoteIssue
from pykantui.workspace.credentials import credential_scope, validate_provider_config


def _bind(provider: ForgejoProvider, handler: httpx.MockTransport) -> None:
    raw = httpx.Client(base_url="https://forge.example/api/v1", transport=handler)
    provider._http = ForgejoClient("https://forge.example/api/v1", client=raw, retries=0)


def _issue(number: int = 7, **changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "id": 107,
        "number": number,
        "title": "Ship Forgejo",
        "body": "Provider details",
        "state": "open",
        "labels": [
            {"id": 1, "name": "status:doing"},
            {"id": 2, "name": "backend"},
        ],
        "assignees": [{"id": 11, "login": "jose", "full_name": "Jose"}],
        "user": {"id": 12, "login": "sam", "full_name": "Sam"},
        "created_at": "2026-08-20T10:00:00Z",
        "updated_at": "2026-08-21T10:00:00Z",
        "due_date": "2026-08-31T00:00:00Z",
        "html_url": "https://forge.example/acme/widgets/issues/7",
    }
    document.update(changes)
    return document


class ForgejoRouteTests(unittest.TestCase):
    def test_repository_components_are_escaped_independently(self) -> None:
        self.assertEqual("/repos/acme/widgets/issues", routes.issues("acme/widgets"))
        self.assertEqual(
            "/repos/acme%20team/widget%23one/issues/7/comments",
            routes.issue_comments("acme team/widget#one", 7),
        )

    def test_repository_must_be_exactly_owner_and_name(self) -> None:
        for value in ("widgets", "acme/widgets/extra", "../widgets", "acme/.."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                routes.issues(value)


class ForgejoClientTests(unittest.TestCase):
    def test_client_adds_api_root_and_uses_forgejo_token_auth(self) -> None:
        client = ForgejoClient.connect("https://forge.example", "secret-token")
        self.addCleanup(client.close)

        self.assertEqual("https://forge.example/api/v1", client.base_url)
        self.assertEqual("token secret-token", client._client.headers["Authorization"])
        self.assertNotIn("secret-token", client.base_url)
        self.assertIn("secret-token", client._sensitive_values)

    def test_client_does_not_duplicate_an_existing_api_root(self) -> None:
        client = ForgejoClient.connect("https://forge.example/api/v1/", "token")
        self.addCleanup(client.close)
        self.assertEqual("https://forge.example/api/v1", client.base_url)

    def test_workspace_credentials_are_https_and_origin_bound(self) -> None:
        with self.assertRaises(ProviderError):
            validate_provider_config(
                "forgejo",
                {"base_url": "http://forge.example", "repo": "acme/widgets"},
            )

        self.assertEqual(
            "https://forge.example",
            credential_scope(
                "forgejo",
                {"base_url": "https://forge.example/installation", "repo": "acme/widgets"},
            ),
        )


class ForgejoProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ForgejoProvider(
            {"base_url": "https://forge.example", "repo": "acme/widgets"},
            {"token": "secret-token"},
        )
        self.addCleanup(self.provider.close)

    def test_provider_is_marked_live_verified(self) -> None:
        self.assertTrue(self.provider.spec.verified)

    def test_verify_and_repository_discovery_are_typed_and_paginated(self) -> None:
        calls: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/api/v1/user":
                return httpx.Response(200, json={"id": 11, "login": "jose", "full_name": "Jose"})
            if request.url.path == "/api/v1/user/repos":
                self.assertEqual("name", request.url.params["order_by"])
                page = int(request.url.params["page"])
                if page == 1:
                    return httpx.Response(
                        200,
                        json=[
                            {
                                "id": index,
                                "name": f"repo-{index}",
                                "full_name": f"acme/repo-{index}",
                                "owner": {"login": "acme"},
                                "has_issues": True,
                                "html_url": f"https://forge.example/acme/repo-{index}",
                            }
                            for index in range(50)
                        ],
                    )
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 51,
                            "name": "private",
                            "full_name": "jose/private",
                            "owner": {"login": "jose"},
                            "private": True,
                            "has_issues": True,
                        }
                    ],
                )
            raise AssertionError(f"unexpected Forgejo request: {request.url}")

        _bind(self.provider, httpx.MockTransport(respond))

        user = self.provider.verify()
        projects = self.provider.list_projects()

        self.assertEqual("jose", user.account_id)
        self.assertEqual("Jose", user.display_name)
        self.assertEqual(51, len(projects))
        self.assertEqual(("jose", "private"), projects[-1].path_parts())
        self.assertTrue(projects[-1].extra["private"])
        self.assertEqual(3, len(calls))

    def test_columns_and_issues_use_status_labels_and_exclude_pull_requests(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/labels"):
                return httpx.Response(
                    200,
                    json=[
                        {"id": 2, "name": "status:doing"},
                        {"id": 3, "name": "status:done"},
                        {"id": 1, "name": "status:todo"},
                        {"id": 4, "name": "backend"},
                    ],
                )
            if request.url.path.endswith("/issues"):
                self.assertEqual("issues", request.url.params["type"])
                return httpx.Response(
                    200,
                    json=[
                        _issue(),
                        _issue(8, id=108, pull_request={"merged": False}),
                    ],
                )
            raise AssertionError(f"unexpected Forgejo request: {request.url}")

        _bind(self.provider, httpx.MockTransport(respond))

        columns = self.provider.list_columns("acme/widgets")
        issues = list(self.provider.iter_issues("acme/widgets"))

        self.assertEqual(
            ["status:todo", "status:doing", "status:done"],
            [column.column_id for column in columns],
        )
        self.assertEqual([0, 1, 2], [column.position for column in columns])
        self.assertEqual(1, len(issues))
        self.assertEqual("widgets#7", issues[0].key)
        self.assertEqual("status:doing", issues[0].column_id)
        self.assertEqual(("backend",), issues[0].labels)
        self.assertEqual(("jose",), issues[0].assignee_ids)
        self.assertEqual("sam", issues[0].reporter_id)
        self.assertEqual(date(2026, 8, 31), issues[0].due_date)

    def test_create_resolves_label_names_to_ids_and_maps_response(self) -> None:
        received: dict[str, object] = {}

        def respond(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/labels"):
                return httpx.Response(
                    200,
                    json=[
                        {"id": 1, "name": "status:doing"},
                        {"id": 2, "name": "backend"},
                    ],
                )
            if request.method == "POST" and request.url.path.endswith("/issues"):
                received.update(__import__("json").loads(request.content))
                return httpx.Response(201, json=_issue())
            raise AssertionError(f"unexpected Forgejo request: {request.method} {request.url}")

        _bind(self.provider, httpx.MockTransport(respond))
        created = self.provider.create_issue(
            "acme/widgets",
            IssueDraft(
                title="Ship Forgejo",
                body="Provider details",
                column_id="status:doing",
                assignee_ids=("jose",),
                labels=("backend",),
                due_date=date(2026, 8, 31),
            ),
        )

        self.assertEqual(["jose"], received["assignees"])
        self.assertEqual([2, 1], received["labels"])
        self.assertEqual("2026-08-31T00:00:00Z", received["due_date"])
        self.assertEqual("107", created.issue_id)

    def test_update_sends_fields_then_replaces_labels_by_name(self) -> None:
        writes: list[tuple[str, str, object]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            writes.append((request.method, request.url.path, __import__("json").loads(request.content)))
            return httpx.Response(200, json={})

        _bind(self.provider, httpx.MockTransport(respond))
        issue = self.provider._to_issue(_issue(), "acme/widgets", self.provider.prefix)
        self.provider.update_issue(
            issue,
            IssueEdit(
                title="Ready",
                column_id="status:done",
                assignee="jose, alex",
                labels=("backend", "release"),
                cleared=("due_date",),
            ),
        )

        self.assertEqual("PATCH", writes[0][0])
        self.assertEqual(
            {"title": "Ready", "assignees": ["jose", "alex"], "unset_due_date": True},
            writes[0][2],
        )
        self.assertEqual("PUT", writes[1][0])
        self.assertEqual({"labels": ["backend", "release", "status:done"]}, writes[1][2])

    def test_move_from_status_label_to_closed_removes_the_old_status_label(self) -> None:
        writes: list[tuple[str, object]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            writes.append((request.method, __import__("json").loads(request.content)))
            return httpx.Response(200, json={})

        _bind(self.provider, httpx.MockTransport(respond))
        issue = self.provider._to_issue(_issue(), "acme/widgets", self.provider.prefix)

        self.provider.update_issue(issue, IssueEdit(column_id="state:closed"))

        self.assertEqual(
            [("PATCH", {"state": "closed"}), ("PUT", {"labels": ["backend"]})],
            writes,
        )

    def test_comments_are_read_and_created_without_putting_tokens_in_urls(self) -> None:
        paths: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            paths.append(str(request.url))
            document = {
                "id": 21,
                "body": "Looks good",
                "user": {"id": 11, "login": "jose"},
                "created_at": "2026-08-22T10:00:00Z",
                "html_url": "https://forge.example/acme/widgets/issues/7#issuecomment-21",
            }
            return httpx.Response(
                201 if request.method == "POST" else 200,
                json=document if request.method == "POST" else [document],
            )

        _bind(self.provider, httpx.MockTransport(respond))
        issue = RemoteIssue(issue_id="107", key="widgets#7", extra={"number": 7})

        comments = list(self.provider.iter_comments("acme/widgets", issue))
        created = self.provider.create_comment(
            "acme/widgets",
            issue,
            CommentDraft(local_id="draft-1", issue_id="107", body="Looks good"),
        )

        self.assertEqual("jose", comments[0].author)
        self.assertEqual("21", created.comment_id)
        self.assertTrue(all("secret-token" not in path for path in paths))


if __name__ == "__main__":
    unittest.main()
