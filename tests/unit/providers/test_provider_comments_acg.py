"""Provider comment contracts for Asana, ClickUp, and GitHub.

All requests use ``httpx.MockTransport``.  These tests deliberately exercise
the public provider methods, so pagination, wire validation, mapping, payload
construction, and the shared no-retry rule are covered together.
"""

from __future__ import annotations

import json
import unittest
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

from pykantui.api import AuthError, JsonHttp, PaginationError, PayloadError, TransportError
from pykantui.providers.asana import AsanaProvider
from pykantui.providers.asana.client import AsanaClient
from pykantui.providers.clickup import ClickUpProvider
from pykantui.providers.clickup.client import ClickUpClient
from pykantui.providers.github import GitHubProvider
from pykantui.providers.github.client import GitHubClient
from pykantui.tracker.base import Provider
from pykantui.tracker.models import CommentDraft, RemoteIssue

_ProviderT = TypeVar("_ProviderT", bound=Provider)


def _draft(issue_id: str, body: str = "Review complete ✓") -> CommentDraft:
    return CommentDraft(local_id="comment-local-1", issue_id=issue_id, body=body)


def _bind_transport(
    provider: _ProviderT,
    client_type: type[JsonHttp],
    base_url: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    retries: int = 0,
) -> _ProviderT:
    raw = httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler))
    provider._http = client_type(base_url, client=raw, retries=retries)
    return provider


def _asana_story(
    comment_id: str,
    *,
    created_at: str,
    subtype: str = "comment_added",
    text: str | None = None,
) -> dict[str, object]:
    return {
        "gid": comment_id,
        "resource_subtype": subtype,
        "type": "comment" if subtype == "comment_added" else "system",
        "text": text if text is not None else f"Asana {comment_id}",
        "created_at": created_at,
        "created_by": {"gid": "user-1", "name": "Ada"},
    }


def _clickup_comment(comment_id: str, date_ms: int, *, text: str | None = None) -> dict[str, object]:
    return {
        "id": comment_id,
        "comment_text": text if text is not None else f"ClickUp {comment_id}",
        "date": str(date_ms),
        "user": {"id": 7, "username": "Grace"},
    }


def _github_comment(comment_id: int, *, body: str | None = None) -> dict[str, object]:
    return {
        "id": comment_id,
        "body": body if body is not None else f"GitHub {comment_id}",
        "user": {"id": 9, "login": "linus"},
        "created_at": f"2026-08-13T12:{comment_id % 60:02d}:00Z",
        "updated_at": f"2026-08-13T12:{comment_id % 60:02d}:30Z",
        "html_url": f"https://github.test/acme/widgets/issues/7#issuecomment-{comment_id}",
    }


class AsanaCommentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = AsanaProvider({"project_id": "project-1"}, {"token": "secret"})
        self.addCleanup(self.provider.close)

    def test_comments_page_by_opaque_offset_and_exclude_system_stories(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            offset = request.url.params.get("offset")
            if offset is None:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            _asana_story(
                                "system-1",
                                created_at="2026-08-13T11:59:00Z",
                                subtype="assigned",
                            ),
                            _asana_story("story-1", created_at="2026-08-13T12:00:00Z"),
                        ],
                        "next_page": {"offset": "opaque-2"},
                    },
                )
            self.assertEqual("opaque-2", offset)
            return httpx.Response(
                200,
                json={
                    "data": [_asana_story("story-2", created_at="2026-08-13T12:01:00Z")],
                    "next_page": None,
                },
            )

        _bind_transport(self.provider, AsanaClient, "https://app.asana.test/api/1.0", handler)
        issue = RemoteIssue(issue_id="task-1", url="https://app.asana.test/0/task-1")

        comments = list(self.provider.iter_comments("project-1", issue))

        self.assertEqual(["story-1", "story-2"], [comment.comment_id for comment in comments])
        self.assertEqual(["Ada", "Ada"], [comment.author for comment in comments])
        self.assertEqual(issue.url, comments[0].url)
        self.assertEqual(2, len(requests))
        self.assertTrue(all(request.url.path == "/api/1.0/tasks/task-1/stories" for request in requests))
        self.assertIn("resource_subtype", requests[0].url.params["opt_fields"])
        self.assertEqual("100", requests[0].url.params["limit"])

    def test_create_comment_sends_only_asana_story_text(self) -> None:
        bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual("POST", request.method)
            bodies.append(json.loads(request.content))
            return httpx.Response(
                201,
                json={
                    "data": _asana_story(
                        "story-new",
                        created_at="2026-08-13T12:02:00Z",
                        text="Review complete ✓",
                    )
                },
            )

        _bind_transport(self.provider, AsanaClient, "https://app.asana.test/api/1.0", handler)
        issue = RemoteIssue(issue_id="task-1", url="https://app.asana.test/0/task-1")

        comment = self.provider.create_comment("project-1", issue, _draft(issue.issue_id))

        self.assertEqual([{"data": {"text": "Review complete ✓"}}], bodies)
        self.assertEqual("story-new", comment.comment_id)
        self.assertEqual("Review complete ✓", comment.body)

    def test_repeated_asana_offset_fails_instead_of_looping(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={
                    "data": [_asana_story(f"story-{calls}", created_at="2026-08-13T12:00:00Z")],
                    "next_page": {"offset": "same-offset"},
                },
            )

        _bind_transport(self.provider, AsanaClient, "https://app.asana.test/api/1.0", handler)

        with self.assertRaisesRegex(PaginationError, "repeated cursor"):
            list(self.provider.iter_comments("project-1", RemoteIssue(issue_id="task-1")))
        self.assertEqual(2, calls)


class ClickUpCommentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ClickUpProvider({"list_id": "list-1"}, {"token": "secret"})
        self.addCleanup(self.provider.close)

    def test_comments_use_date_and_id_cursor_and_are_returned_chronologically(self) -> None:
        requests: list[httpx.Request] = []
        newest_page = [
            _clickup_comment(f"comment-{number}", number * 1_000)
            for number in range(26, 1, -1)
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                self.assertNotIn("start", request.url.params)
                self.assertNotIn("start_id", request.url.params)
                return httpx.Response(200, json={"comments": newest_page})
            self.assertEqual("2000", request.url.params["start"])
            self.assertEqual("comment-2", request.url.params["start_id"])
            return httpx.Response(
                200,
                json={
                    "comments": [
                        _clickup_comment("comment-2", 2_000),
                        _clickup_comment("comment-1", 1_000),
                    ]
                },
            )

        _bind_transport(self.provider, ClickUpClient, "https://api.clickup.test/api/v2", handler)
        issue = RemoteIssue(issue_id="task-1", url="https://app.clickup.test/t/task-1")

        comments = list(self.provider.iter_comments("list-1", issue))

        self.assertEqual([f"comment-{number}" for number in range(1, 27)], [item.comment_id for item in comments])
        self.assertEqual("Grace", comments[0].author)
        self.assertEqual(issue.url, comments[-1].url)
        self.assertEqual(2, len(requests))

    def test_repeated_clickup_cursor_fails_instead_of_looping_or_truncating(self) -> None:
        calls = 0
        page = [_clickup_comment(f"comment-{number}", number * 1_000) for number in range(26, 1, -1)]

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"comments": page})

        _bind_transport(self.provider, ClickUpClient, "https://api.clickup.test/api/v2", handler)

        with self.assertRaisesRegex(PaginationError, "repeated cursor"):
            list(self.provider.iter_comments("list-1", RemoteIssue(issue_id="task-1")))
        self.assertEqual(2, calls)

    def test_rich_clickup_comment_falls_back_to_text_fragments(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {
                            "id": "rich-1",
                            "date": "1723636800000",
                            "user": {"id": 7, "username": "Grace"},
                            "comment": [{"text": "Review "}, {"text": "complete"}],
                        }
                    ]
                },
            )

        _bind_transport(self.provider, ClickUpClient, "https://api.clickup.test/api/v2", handler)

        comments = list(self.provider.iter_comments("list-1", RemoteIssue(issue_id="task-1")))

        self.assertEqual("Review complete", comments[0].body)

    def test_threaded_replies_are_loaded_only_for_parents_that_have_them(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/task/task-1/comment"):
                return httpx.Response(
                    200,
                    json={
                        "comments": [
                            {
                                "id": 2,
                                "date": "2000",
                                "comment_text": "parent with reply",
                                "reply_count": 1,
                            },
                            {
                                "id": 1,
                                "date": "1000",
                                "comment_text": "plain parent",
                                "reply_count": 0,
                            },
                        ]
                    },
                )
            self.assertTrue(request.url.path.endswith("/comment/2/reply"))
            return httpx.Response(
                200,
                json={
                    "comments": [{
                        "id": 3,
                        "date": "3000",
                        "comment_text": "threaded reply",
                    }]
                },
            )

        _bind_transport(self.provider, ClickUpClient, "https://api.clickup.test/api/v2", handler)

        comments = list(self.provider.iter_comments("list-1", RemoteIssue(issue_id="task-1")))

        self.assertEqual(["1", "2", "3"], [comment.comment_id for comment in comments])
        self.assertEqual("2", comments[-1].parent_id)
        self.assertEqual(2, len(requests))

    def test_create_comment_refetches_partial_response_for_canonical_author(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                self.assertEqual(
                    {"comment_text": "Review complete ✓", "notify_all": False},
                    json.loads(request.content),
                )
                return httpx.Response(200, json={"id": "comment-new", "hist_id": "h-1", "date": 1_723_636_800_000})
            return httpx.Response(
                200,
                json={
                    "comments": [
                        _clickup_comment(
                            "comment-new",
                            1_723_636_800_000,
                            text="Review complete ✓",
                        )
                    ]
                },
            )

        _bind_transport(self.provider, ClickUpClient, "https://api.clickup.test/api/v2", handler)
        issue = RemoteIssue(issue_id="task-1", url="https://app.clickup.test/t/task-1")

        comment = self.provider.create_comment("list-1", issue, _draft(issue.issue_id))

        self.assertEqual(["POST", "GET"], [request.method for request in requests])
        self.assertEqual("comment-new", comment.comment_id)
        self.assertEqual("Grace", comment.author)
        self.assertEqual("Review complete ✓", comment.body)


class GitHubCommentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = GitHubProvider({"repo": "acme/widgets"}, {"token": "secret"})
        self.addCleanup(self.provider.close)

    def test_comments_page_at_one_hundred_and_keep_github_urls(self) -> None:
        pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            pages.append(page)
            if page == 1:
                return httpx.Response(200, json=[_github_comment(number) for number in range(1, 101)])
            return httpx.Response(200, json=[_github_comment(101)])

        _bind_transport(self.provider, GitHubClient, "https://api.github.test", handler)
        issue = RemoteIssue(issue_id="node-7", extra={"number": 7})

        comments = list(self.provider.iter_comments("acme/widgets", issue))

        self.assertEqual([1, 2], pages)
        self.assertEqual(101, len(comments))
        self.assertEqual("1", comments[0].comment_id)
        self.assertEqual("linus", comments[0].author)
        self.assertTrue(comments[-1].url.endswith("#issuecomment-101"))

    def test_create_comment_sends_markdown_body_and_maps_canonical_response(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual({"body": "Review complete ✓"}, json.loads(request.content))
            return httpx.Response(201, json=_github_comment(102, body="Review complete ✓"))

        _bind_transport(self.provider, GitHubClient, "https://api.github.test", handler)
        issue = RemoteIssue(issue_id="node-7", extra={"number": 7})

        comment = self.provider.create_comment("acme/widgets", issue, _draft(issue.issue_id))

        self.assertEqual("/repos/acme/widgets/issues/7/comments", requests[0].url.path)
        self.assertEqual("102", comment.comment_id)
        self.assertEqual("Review complete ✓", comment.body)

    def test_deleted_github_author_and_body_are_safe_empty_values(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            comment = _github_comment(103)
            comment["body"] = None
            comment["user"] = None
            return httpx.Response(200, json=[comment])

        _bind_transport(self.provider, GitHubClient, "https://api.github.test", handler)

        comments = list(
            self.provider.iter_comments(
                "acme/widgets",
                RemoteIssue(issue_id="node-7", extra={"number": 7}),
            )
        )

        self.assertEqual("", comments[0].body)
        self.assertEqual("", comments[0].author)


class CommentTransportEdgeTests(unittest.TestCase):
    def _providers(self) -> list[tuple[Provider, type[JsonHttp], str, RemoteIssue]]:
        return [
            (
                AsanaProvider({"project_id": "project-1"}, {"token": "secret"}),
                AsanaClient,
                "https://app.asana.test/api/1.0",
                RemoteIssue(issue_id="task-1"),
            ),
            (
                ClickUpProvider({"list_id": "list-1"}, {"token": "secret"}),
                ClickUpClient,
                "https://api.clickup.test/api/v2",
                RemoteIssue(issue_id="task-1"),
            ),
            (
                GitHubProvider({"repo": "acme/widgets"}, {"token": "secret"}),
                GitHubClient,
                "https://api.github.test",
                RemoteIssue(issue_id="node-7", extra={"number": 7}),
            ),
        ]

    def test_capabilities_declare_read_and_create(self) -> None:
        for provider, _, _, _ in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)
                self.assertTrue(provider.spec.capabilities.read_comments)
                self.assertTrue(provider.spec.capabilities.create_comments)

    def test_auth_errors_remain_typed(self) -> None:
        for provider, client_type, base_url, issue in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)

                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(401, json={"message": "bad token"})

                _bind_transport(provider, client_type, base_url, handler)
                with self.assertRaises(AuthError):
                    list(provider.iter_comments("project-1", issue))

    def test_malformed_comment_collections_raise_payload_error(self) -> None:
        malformed: dict[str, object] = {
            "asana": {"unexpected": []},
            "clickup": {"unexpected": []},
            "github": {"unexpected": []},
        }
        for provider, client_type, base_url, issue in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)

                def handler(request: httpx.Request, *, name: str = provider.spec.name) -> httpx.Response:
                    return httpx.Response(200, json=malformed[name])

                _bind_transport(provider, client_type, base_url, handler)
                with self.assertRaises(PayloadError):
                    list(provider.iter_comments("project-1", issue))

    def test_comment_posts_are_never_automatically_retried(self) -> None:
        for provider, client_type, base_url, issue in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)
                calls = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(503, json={"message": "temporary"})

                _bind_transport(provider, client_type, base_url, handler, retries=3)
                with self.assertRaises(TransportError):
                    provider.create_comment("project-1", issue, _draft(issue.issue_id))
                self.assertEqual(1, calls)

    def test_malformed_create_responses_raise_payload_error(self) -> None:
        for provider, client_type, base_url, issue in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)

                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(201, json={"unexpected": "shape"})

                _bind_transport(provider, client_type, base_url, handler)
                with self.assertRaises(PayloadError):
                    provider.create_comment("project-1", issue, _draft(issue.issue_id))


if __name__ == "__main__":
    unittest.main()
