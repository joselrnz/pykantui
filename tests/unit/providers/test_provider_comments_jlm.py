"""Comment API contracts for Jira, Linear, and Monday.com."""

from __future__ import annotations

import unittest
from collections.abc import Callable, Mapping
from typing import TypeVar, cast
from unittest.mock import patch

import httpx

from pykantui.api import AuthError, JsonHttp, JsonValue, PaginationError, PayloadError, TransportError
from pykantui.providers.jira.provider import JiraProvider
from pykantui.providers.linear.provider import LinearProvider
from pykantui.providers.monday.client import MondayClient
from pykantui.providers.monday.provider import API_VERSION, MondayProvider
from pykantui.tracker.base import Provider
from pykantui.tracker.models import CommentDraft, RemoteIssue

ISSUE = RemoteIssue(issue_id="issue-1", key="JPT-4", title="Comments")
DRAFT = CommentDraft(local_id="draft-1", issue_id="issue-1", body="Hello <script>\nsecond line")
_MISSING = object()
_ProviderT = TypeVar("_ProviderT", bound=Provider)


def _bind_transport(
    provider: _ProviderT,
    base_url: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    retries: int = 0,
) -> _ProviderT:
    raw = httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler))
    provider._http = JsonHttp(base_url, client=raw, retries=retries)
    return provider


class RecordingHttp:
    """Small injectable JSON transport with provider-specific responders."""

    def __init__(self, responder: object) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str, object]] = []

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        ttl: float = 0.0,
        label: str = "",
    ) -> JsonValue:
        del ttl, label
        self.calls.append(("GET", path, params))
        return cast(JsonValue, self.responder("GET", path, params))  # type: ignore[operator]

    def post(
        self,
        path: str,
        body: JsonValue = None,
        params: Mapping[str, object] | None = None,
    ) -> JsonValue:
        self.calls.append(("POST", path, (body, params)))
        return cast(JsonValue, self.responder("POST", path, (body, params)))  # type: ignore[operator]

    def graphql(
        self,
        query: str,
        variables: Mapping[str, JsonValue] | None = None,
        *,
        path: str = "",
    ) -> JsonValue:
        del path
        self.calls.append(("GRAPHQL", query, variables))
        return cast(JsonValue, self.responder("GRAPHQL", query, variables))  # type: ignore[operator]

    def put(self, path: str, body: JsonValue = None, params: object = None) -> JsonValue:
        raise AssertionError((path, body, params))

    patch = put
    delete = put

    def close(self) -> None:
        return None


class JiraCommentTests(unittest.TestCase):
    def provider(self, responder: object) -> tuple[JiraProvider, RecordingHttp]:
        provider = JiraProvider(
            {"base_url": "https://acme.atlassian.net"},
            {"email": "a@example.com", "token": "secret"},
        )
        transport = RecordingHttp(responder)
        provider._http = cast(JsonHttp, transport)
        return provider, transport

    def test_reads_every_offset_page_and_maps_adf(self) -> None:
        def respond(method: str, path: str, value: object) -> object:
            self.assertEqual("GET", method)
            self.assertEqual("/rest/api/3/issue/JPT-4/comment", path)
            start = int(str(cast(Mapping[str, object], value)["startAt"]))
            return {
                "startAt": start,
                "maxResults": 2,
                "total": 3,
                "comments": [
                    {
                        "id": str(index),
                        "body": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": f"reply {index}"}]}
                            ],
                        },
                        "author": {"accountId": f"a-{index}", "displayName": f"Author {index}"},
                        "created": "2026-08-13T12:30:00.000+0000",
                        "updated": "2026-08-13T12:31:00.000+0000",
                        "self": f"https://acme.atlassian.net/rest/api/3/issue/JPT-4/comment/{index}",
                    }
                    for index in range(start, min(start + 2, 3))
                ],
            }

        provider, transport = self.provider(respond)

        comments = list(provider.iter_comments("P1", ISSUE))

        self.assertEqual(["0", "1", "2"], [comment.comment_id for comment in comments])
        self.assertEqual("reply 2", comments[-1].body)
        self.assertEqual("Author 2", comments[-1].author)
        self.assertEqual(2, len(transport.calls))

    def test_create_uses_adf_and_maps_the_returned_comment(self) -> None:
        def respond(method: str, path: str, value: object) -> object:
            self.assertEqual(("POST", "/rest/api/3/issue/JPT-4/comment"), (method, path))
            body, params = cast(tuple[Mapping[str, object], object], value)
            self.assertIsNone(params)
            document = cast(Mapping[str, object], body["body"])
            self.assertEqual("doc", document["type"])
            paragraph = cast(list[Mapping[str, object]], document["content"])[0]
            nodes = cast(list[Mapping[str, object]], paragraph["content"])
            self.assertEqual("Hello <script>", nodes[0]["text"])
            self.assertEqual("hardBreak", nodes[1]["type"])
            self.assertEqual("second line", nodes[2]["text"])
            return {
                "id": "99",
                "body": document,
                "author": {"accountId": "me", "displayName": "Me"},
                "created": "2026-08-13T12:30:00.000+0000",
            }

        provider, _ = self.provider(respond)

        created = provider.create_comment("P1", ISSUE, DRAFT)

        self.assertEqual("99", created.comment_id)
        self.assertEqual("Hello <script>  \nsecond line", created.body)

    def test_rejects_missing_null_and_blank_comment_ids_as_payload_errors(self) -> None:
        for invalid_id in (_MISSING, None, "", "   "):
            comment: dict[str, object] = {"body": "unsafe to cache"}
            if invalid_id is not _MISSING:
                comment["id"] = invalid_id

            def respond(method: str, path: str, value: object, *, row: object = comment) -> object:
                del method, path, value
                return {"startAt": 0, "maxResults": 100, "total": 1, "comments": [row]}

            provider, _ = self.provider(respond)
            with self.subTest(invalid_id=invalid_id), self.assertRaises(PayloadError):
                list(provider.iter_comments("P1", ISSUE))

    def test_empty_comment_envelope_fails_closed(self) -> None:
        provider, _ = self.provider(lambda method, path, value: {})

        with self.assertRaises(PayloadError):
            list(provider.iter_comments("P1", ISSUE))


class LinearCommentTests(unittest.TestCase):
    def provider(self, responder: object) -> tuple[LinearProvider, RecordingHttp]:
        provider = LinearProvider({}, {"token": "secret"})
        transport = RecordingHttp(responder)
        provider._http = cast(JsonHttp, transport)
        return provider, transport

    def test_reads_relay_pages_and_supports_bot_authors(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            self.assertEqual("GRAPHQL", method)
            self.assertIn("comments", query)
            cursor = cast(Mapping[str, object], variables).get("cursor")
            node = {
                "id": "c-1" if cursor is None else "c-2",
                "issueId": ISSUE.issue_id,
                "body": "first" if cursor is None else "second",
                "url": "https://linear.app/comment/c-1",
                "createdAt": "2026-08-13T12:30:00Z",
                "updatedAt": "2026-08-13T12:31:00Z",
                "parentId": None,
                "user": None,
                "botActor": {"id": "bot", "name": "Release bot"},
                "externalUser": None,
            }
            return {
                "issue": {
                    "comments": {
                        "nodes": [node],
                        "pageInfo": {"hasNextPage": cursor is None, "endCursor": "next" if cursor is None else None},
                    }
                }
            }

        provider, transport = self.provider(respond)

        comments = list(provider.iter_comments("team", ISSUE))

        self.assertEqual(["c-1", "c-2"], [comment.comment_id for comment in comments])
        self.assertTrue(all(comment.author == "Release bot" for comment in comments))
        self.assertEqual(2, len(transport.calls))

    def test_create_sends_markdown_unchanged(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            self.assertEqual("GRAPHQL", method)
            self.assertIn("commentCreate", query)
            payload = cast(Mapping[str, object], cast(Mapping[str, object], variables)["input"])
            self.assertEqual(DRAFT.body, payload["body"])
            self.assertEqual(ISSUE.issue_id, payload["issueId"])
            return {
                "commentCreate": {
                    "success": True,
                    "comment": {
                        "id": "new",
                        "issueId": ISSUE.issue_id,
                        "body": DRAFT.body,
                        "createdAt": "2026-08-13T12:30:00Z",
                        "user": {"id": "me", "name": "Me", "displayName": "Me"},
                    },
                }
            }

        provider, _ = self.provider(respond)
        self.assertEqual("new", provider.create_comment("team", ISSUE, DRAFT).comment_id)

    def test_accepted_create_without_a_canonical_record_is_ambiguous_payload(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            del method, query, variables
            return {"commentCreate": {"success": True, "comment": None}}

        provider, _ = self.provider(respond)

        with self.assertRaises(PayloadError):
            provider.create_comment("team", ISSUE, DRAFT)

    def test_missing_next_cursor_fails_instead_of_truncating_the_thread(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            del method, query, variables
            return {
                "issue": {
                    "comments": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": True, "endCursor": None},
                    }
                }
            }

        provider, _ = self.provider(respond)

        with self.assertRaises(PaginationError):
            list(provider.iter_comments("team", ISSUE))

    def test_rejects_missing_null_and_blank_comment_ids_as_payload_errors(self) -> None:
        for invalid_id in (_MISSING, None, "", "   "):
            comment: dict[str, object] = {"body": "unsafe to cache"}
            if invalid_id is not _MISSING:
                comment["id"] = invalid_id

            def respond(method: str, query: str, variables: object, *, row: object = comment) -> object:
                del method, query, variables
                return {
                    "issue": {
                        "comments": {
                            "nodes": [row],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }

            provider, _ = self.provider(respond)
            with self.subTest(invalid_id=invalid_id), self.assertRaises(PayloadError):
                list(provider.iter_comments("team", ISSUE))

    def test_missing_or_null_issue_envelope_fails_closed(self) -> None:
        responses: list[dict[str, object]] = [{}, {"issue": None}, {"issue": {}}]
        for response in responses:
            provider, _ = self.provider(
                lambda method, query, variables, *, payload=response: payload
            )
            with self.subTest(response=response), self.assertRaises(PayloadError):
                list(provider.iter_comments("team", ISSUE))


class MondayCommentTests(unittest.TestCase):
    def provider(self, responder: object) -> tuple[MondayProvider, RecordingHttp]:
        provider = MondayProvider({"board_id": "board-1"}, {"token": "secret"})
        transport = RecordingHttp(responder)
        provider._http = cast(JsonHttp, transport)
        return provider, transport

    def test_uses_the_current_stable_api_version_for_comment_fields(self) -> None:
        self.assertEqual("2026-07", API_VERSION)
        client = MondayClient.connect("https://api.monday.test/v2", "secret", api_version=API_VERSION)
        try:
            self.assertEqual("2026-07", client._client.headers["API-Version"])
        finally:
            client.close()

    def test_reads_all_update_pages_and_prefers_plain_text(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            self.assertEqual("GRAPHQL", method)
            self.assertIn("updates", query)
            page = int(str(cast(Mapping[str, object], variables)["page"]))
            count = 100 if page in (1, 2) else 0
            updates = [
                {
                    "id": f"u-{page}-{index}",
                    "body": "<p>unsafe-looking provider HTML</p>",
                    "text_body": f"reply {page}-{index}",
                    "created_at": f"2026-08-13T12:{page:02d}:{index % 60:02d}Z",
                    "updated_at": "2026-08-13T13:00:00Z",
                    "creator_id": "7",
                    "creator": {"id": "7", "name": "Author"},
                }
                for index in range(count)
            ]
            return {"items": [{"id": ISSUE.issue_id, "updates": updates}]}

        provider, transport = self.provider(respond)

        comments = list(provider.iter_comments("board-1", ISSUE))

        self.assertEqual(200, len(comments))
        self.assertEqual("u-1-0", comments[0].comment_id)
        self.assertEqual({f"u-{page}-{index}" for page in (1, 2) for index in range(100)}, {
            comment.comment_id for comment in comments
        })
        self.assertEqual(3, len(transport.calls))

    def test_nested_update_replies_are_preserved_with_their_parent(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            del method, variables
            self.assertIn("replies", query)
            return {
                "items": [{
                    "id": ISSUE.issue_id,
                    "updates": [{
                        "id": "root",
                        "text_body": "root comment",
                        "created_at": "2026-08-13T12:30:00Z",
                        "creator": {"id": "1", "name": "Root author"},
                        "replies": [{
                            "id": "reply",
                            "text_body": "nested reply",
                            "created_at": "2026-08-13T12:31:00Z",
                            "creator": {"id": "2", "name": "Reply author"},
                        }],
                    }],
                }]
            }

        provider, _ = self.provider(respond)

        comments = list(provider.iter_comments("board-1", ISSUE))

        self.assertEqual(["root", "reply"], [comment.comment_id for comment in comments])
        self.assertEqual("root", comments[1].parent_id)
        self.assertEqual("Reply author", comments[1].author)

    def test_maps_large_nested_reply_arrays_without_local_truncation(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            del method, variables
            self.assertIn("replies {", query)
            self.assertNotIn("replies(", query)
            replies = [
                {
                    "id": f"reply-{index}",
                    "text_body": f"reply {index}",
                    "created_at": "2026-08-13T12:31:00Z",
                }
                for index in range(101)
            ]
            return {
                "items": [{
                    "id": ISSUE.issue_id,
                    "updates": [{
                        "id": "root",
                        "text_body": "root comment",
                        "created_at": "2026-08-13T12:30:00Z",
                        "replies": replies,
                    }],
                }]
            }

        provider, transport = self.provider(respond)

        comments = list(provider.iter_comments("board-1", ISSUE))

        self.assertEqual(102, len(comments))
        self.assertEqual("root", comments[0].comment_id)
        self.assertEqual(
            {f"reply-{index}" for index in range(101)},
            {comment.comment_id for comment in comments[1:]},
        )
        self.assertTrue(all(comment.parent_id == "root" for comment in comments[1:]))
        self.assertEqual(1, len(transport.calls))

    def test_create_escapes_html_in_local_markdown(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            self.assertEqual("GRAPHQL", method)
            self.assertIn("create_update", query)
            body = str(cast(Mapping[str, object], variables)["body"])
            self.assertIn("&lt;script&gt;", body)
            self.assertNotIn("<script>", body)
            self.assertIn("<br>", body)
            return {
                "create_update": {
                    "id": "new",
                    "body": body,
                    "text_body": DRAFT.body,
                    "created_at": "2026-08-13T12:30:00Z",
                    "creator": {"id": "me", "name": "Me"},
                }
            }

        provider, _ = self.provider(respond)
        self.assertEqual(DRAFT.body, provider.create_comment("board-1", ISSUE, DRAFT).body)

    def test_page_safety_ceiling_fails_instead_of_truncating_updates(self) -> None:
        def respond(method: str, query: str, variables: object) -> object:
            del method, query, variables
            return {
                "items": [{
                    "id": ISSUE.issue_id,
                    "updates": [{"id": str(index)} for index in range(100)],
                }]
            }

        provider, _ = self.provider(respond)

        with (
            patch("pykantui.providers.monday.client.range", return_value=range(1, 3), create=True),
            self.assertRaises(PaginationError),
        ):
            list(provider.iter_comments("board-1", ISSUE))

    def test_rejects_missing_null_and_blank_update_ids_as_payload_errors(self) -> None:
        for invalid_id in (_MISSING, None, "", "   "):
            update: dict[str, object] = {"text_body": "unsafe to cache"}
            if invalid_id is not _MISSING:
                update["id"] = invalid_id

            def respond(method: str, query: str, variables: object, *, row: object = update) -> object:
                del method, query, variables
                return {"items": [{"id": ISSUE.issue_id, "updates": [row]}]}

            provider, _ = self.provider(respond)
            with self.subTest(invalid_id=invalid_id), self.assertRaises(PayloadError):
                list(provider.iter_comments("board-1", ISSUE))

    def test_missing_or_wrong_item_envelope_fails_closed(self) -> None:
        responses: list[dict[str, object]] = [
            {},
            {"items": []},
            {"items": [{"id": "wrong", "updates": []}]},
        ]
        for response in responses:
            provider, _ = self.provider(
                lambda method, query, variables, *, payload=response: payload
            )
            with self.subTest(response=response), self.assertRaises(PayloadError):
                list(provider.iter_comments("board-1", ISSUE))


class CapabilityTests(unittest.TestCase):
    def test_all_three_advertise_read_and_create(self) -> None:
        for provider in (JiraProvider, LinearProvider, MondayProvider):
            with self.subTest(provider=provider.spec.name):
                self.assertTrue(provider.spec.capabilities.read_comments)
                self.assertTrue(provider.spec.capabilities.create_comments)


class CommentTransportEdgeTests(unittest.TestCase):
    def _providers(self) -> list[Provider]:
        return [
            JiraProvider(
                {"base_url": "https://jira.test"},
                {"email": "a@example.com", "token": "secret"},
            ),
            LinearProvider({}, {"token": "secret"}),
            MondayProvider({"board_id": "board-1"}, {"token": "secret"}),
        ]

    @staticmethod
    def _response(provider: Provider, data: object) -> object:
        if provider.spec.name in {"linear", "monday"}:
            return {"data": data}
        return data

    def test_unauthorized_and_forbidden_reads_remain_typed(self) -> None:
        for status in (401, 403):
            for provider in self._providers():
                with self.subTest(provider=provider.spec.name, status=status):
                    self.addCleanup(provider.close)

                    def handler(request: httpx.Request, *, code: int = status) -> httpx.Response:
                        return httpx.Response(code, json={"message": "bad token"})

                    _bind_transport(provider, "https://provider.test", handler)
                    with self.assertRaises(AuthError):
                        list(provider.iter_comments("P1", ISSUE))

    def test_malformed_comment_collections_raise_payload_error(self) -> None:
        for provider in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)

                def handler(request: httpx.Request, *, current: Provider = provider) -> httpx.Response:
                    return httpx.Response(200, json=self._response(current, {}))

                _bind_transport(provider, "https://provider.test", handler)
                with self.assertRaises(PayloadError):
                    list(provider.iter_comments("P1", ISSUE))

    def test_comment_posts_are_never_automatically_retried(self) -> None:
        for provider in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)
                calls = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(503, json={"message": "temporary"})

                _bind_transport(provider, "https://provider.test", handler, retries=3)
                with self.assertRaises(TransportError):
                    provider.create_comment("P1", ISSUE, DRAFT)
                self.assertEqual(1, calls)

    def test_malformed_create_responses_raise_payload_error(self) -> None:
        for provider in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)

                def handler(request: httpx.Request, *, current: Provider = provider) -> httpx.Response:
                    return httpx.Response(201, json=self._response(current, {"unexpected": "shape"}))

                _bind_transport(provider, "https://provider.test", handler)
                with self.assertRaises(PayloadError):
                    provider.create_comment("P1", ISSUE, DRAFT)


if __name__ == "__main__":
    unittest.main()
