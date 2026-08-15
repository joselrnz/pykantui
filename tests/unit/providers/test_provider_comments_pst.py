"""Comment API contracts for Plane, Shortcut, and Trello."""

from __future__ import annotations

import unittest
from collections.abc import Callable, Mapping
from typing import TypeVar, cast

import httpx

from pykantui.api import AuthError, JsonHttp, JsonValue, PaginationError, PayloadError, TransportError
from pykantui.providers.plane.provider import PlaneProvider
from pykantui.providers.shortcut.provider import ShortcutProvider
from pykantui.providers.trello.provider import TrelloProvider
from pykantui.tracker.base import Provider
from pykantui.tracker.models import CommentDraft, RemoteIssue

ISSUE = RemoteIssue(issue_id="issue-1", key="CARD-1", title="Comments")
DRAFT = CommentDraft(local_id="draft-1", issue_id=ISSUE.issue_id, body="Hello <b>\nsecond")
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
    def __init__(self, responder: object) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str, object]] = []

    def get(self, path: str, params: object = None, **kwargs: object) -> JsonValue:
        del kwargs
        self.calls.append(("GET", path, params))
        return cast(JsonValue, self.responder("GET", path, params))  # type: ignore[operator]

    def post(self, path: str, body: JsonValue = None, params: object = None) -> JsonValue:
        self.calls.append(("POST", path, (body, params)))
        return cast(JsonValue, self.responder("POST", path, (body, params)))  # type: ignore[operator]

    def put(self, path: str, body: JsonValue = None, params: object = None) -> JsonValue:
        raise AssertionError((path, body, params))

    patch = put
    delete = put

    def graphql(self, query: str, variables: object = None, *, path: str = "") -> JsonValue:
        raise AssertionError((query, variables, path))

    def close(self) -> None:
        return None


class PlaneCommentTests(unittest.TestCase):
    def provider(self, responder: object) -> tuple[PlaneProvider, RecordingHttp]:
        provider = PlaneProvider(
            {"workspace": "acme", "project_id": "P1", "base_url": "https://plane.acme.test"},
            {"token": "secret"},
        )
        http = RecordingHttp(responder)
        provider._http = cast(JsonHttp, http)
        provider._members_by_project["P1"] = {"user-uuid": "Plane user", "me": "Me"}
        return provider, http

    def test_reads_cursor_pages_from_current_work_item_route(self) -> None:
        def respond(method: str, path: str, params: object) -> object:
            self.assertEqual("GET", method)
            self.assertEqual(
                "/api/v1/workspaces/acme/projects/P1/work-items/issue-1/comments/",
                path,
            )
            cursor = cast(Mapping[str, object], params).get("cursor")
            return {
                "results": [{
                    "id": "c-1" if cursor is None else "c-2",
                    "comment_html": "<p>provider html</p>",
                    "comment_stripped": "first" if cursor is None else "second",
                    "created_at": "2026-08-13T12:30:00Z",
                    "updated_at": "2026-08-13T12:31:00Z",
                    "actor": {"id": "u-1", "display_name": "Plane user"},
                }],
                "next_page_results": cursor is None,
                "next_cursor": "next" if cursor is None else "end",
            }

        provider, http = self.provider(respond)
        comments = list(provider.iter_comments("P1", ISSUE))

        self.assertEqual(["c-1", "c-2"], [comment.comment_id for comment in comments])
        self.assertEqual(["first", "second"], [comment.body for comment in comments])
        self.assertEqual(2, len(http.calls))

    def test_reads_the_documented_uuid_actor_shape(self) -> None:
        def respond(method: str, path: str, params: object) -> object:
            self.assertEqual("GET", method)
            return {
                "results": [{
                    "id": "c-uuid",
                    "comment_stripped": "from a UUID actor",
                    "created_at": "2026-08-13T12:30:00Z",
                    "created_by": "user-uuid",
                    "actor": "user-uuid",
                }],
                "next_page_results": False,
                "next_cursor": "",
            }

        provider, _ = self.provider(respond)
        comment = list(provider.iter_comments("P1", ISSUE))[0]

        self.assertEqual("user-uuid", comment.author_id)
        self.assertEqual("Plane user", comment.author)

    def test_create_uses_escaped_html(self) -> None:
        def respond(method: str, path: str, value: object) -> object:
            self.assertEqual("POST", method)
            body, params = cast(tuple[Mapping[str, object], object], value)
            self.assertIsNone(params)
            self.assertEqual("<p>Hello &lt;b&gt;<br>second</p>", body["comment_html"])
            self.assertEqual("INTERNAL", body["access"])
            return {
                "id": "new",
                "comment_html": body["comment_html"],
                "comment_stripped": DRAFT.body,
                "created_at": "2026-08-13T12:30:00Z",
                "actor": {"id": "me", "display_name": "Me"},
            }

        provider, _ = self.provider(respond)
        self.assertEqual(DRAFT.body, provider.create_comment("P1", ISSUE, DRAFT).body)

    def test_rejects_missing_null_and_blank_comment_ids_as_payload_errors(self) -> None:
        for invalid_id in (_MISSING, None, "", "   "):
            comment: dict[str, object] = {"comment_stripped": "unsafe to cache"}
            if invalid_id is not _MISSING:
                comment["id"] = invalid_id

            def respond(method: str, path: str, params: object, *, row: object = comment) -> object:
                del method, path, params
                return {"results": [row], "next_page_results": False, "next_cursor": ""}

            provider, _ = self.provider(respond)
            with self.subTest(invalid_id=invalid_id), self.assertRaises(PayloadError):
                list(provider.iter_comments("P1", ISSUE))

    def test_empty_comment_envelope_fails_closed(self) -> None:
        provider, _ = self.provider(lambda method, path, value: {})

        with self.assertRaises(PayloadError):
            list(provider.iter_comments("P1", ISSUE))


class ShortcutCommentTests(unittest.TestCase):
    def provider(self, responder: object) -> tuple[ShortcutProvider, RecordingHttp]:
        provider = ShortcutProvider({"workflow_id": "10"}, {"token": "secret"})
        http = RecordingHttp(responder)
        provider._http = cast(JsonHttp, http)
        provider._member_names = {"7": "Shortcut user"}
        return provider, http

    def test_reads_the_complete_story_comment_array_and_deleted_records(self) -> None:
        def respond(method: str, path: str, value: object) -> object:
            self.assertEqual(("GET", "/stories/issue-1/comments"), (method, path))
            self.assertIsNone(value)
            return [
                {
                    "id": 1,
                    "story_id": "issue-1",
                    "author_id": "7",
                    "text": "hello",
                    "created_at": "2026-08-13T12:30:00Z",
                    "app_url": "https://app.shortcut.com/acme/story/1#comment-1",
                },
                {"id": 2, "story_id": "issue-1", "author_id": "gone", "text": None, "deleted": True},
            ]

        provider, _ = self.provider(respond)
        comments = list(provider.iter_comments("10", ISSUE))

        self.assertEqual("Shortcut user", comments[0].author)
        self.assertEqual("", comments[1].body)
        self.assertTrue(comments[1].deleted)

    def test_nullable_authors_and_shuffled_positions_stay_chronological(self) -> None:
        def respond(method: str, path: str, value: object) -> object:
            del method, path, value
            return [
                {"id": 2, "story_id": "issue-1", "author_id": None, "text": None, "deleted": True, "position": 2},
                {"id": 1, "story_id": "issue-1", "author_id": "7", "text": "first", "position": 1},
            ]

        provider, _ = self.provider(respond)
        comments = list(provider.iter_comments("10", ISSUE))

        self.assertEqual(["1", "2"], [comment.comment_id for comment in comments])
        self.assertEqual("", comments[1].author_id)
        self.assertTrue(comments[1].deleted)

    def test_create_sends_only_server_owned_text(self) -> None:
        def respond(method: str, path: str, value: object) -> object:
            body, params = cast(tuple[Mapping[str, object], object], value)
            self.assertEqual(("POST", "/stories/issue-1/comments"), (method, path))
            self.assertEqual({"text": DRAFT.body}, body)
            self.assertIsNone(params)
            return {"id": 3, "story_id": "issue-1", "author_id": "7", "text": DRAFT.body}

        provider, _ = self.provider(respond)
        self.assertEqual("3", provider.create_comment("10", ISSUE, DRAFT).comment_id)

    def test_rejects_missing_null_and_blank_comment_ids_as_payload_errors(self) -> None:
        for invalid_id in (_MISSING, None, "", "   "):
            comment: dict[str, object] = {"text": "unsafe to cache"}
            if invalid_id is not _MISSING:
                comment["id"] = invalid_id

            def respond(method: str, path: str, value: object, *, row: object = comment) -> object:
                del method, path, value
                return [row]

            provider, _ = self.provider(respond)
            with self.subTest(invalid_id=invalid_id), self.assertRaises(PayloadError):
                list(provider.iter_comments("10", ISSUE))


class TrelloCommentTests(unittest.TestCase):
    def provider(self, responder: object) -> tuple[TrelloProvider, RecordingHttp]:
        provider = TrelloProvider({"board_id": "board"}, {"key": "key", "token": "token"})
        http = RecordingHttp(responder)
        provider._http = cast(JsonHttp, http)
        return provider, http

    def test_reads_comment_actions_with_stable_before_pagination(self) -> None:
        def action(index: int) -> dict[str, object]:
            return {
                "id": f"a-{index:04d}",
                "type": "commentCard",
                "date": "2026-08-13T12:30:00Z",
                "idMemberCreator": "7",
                "data": {"text": f"reply {index}"},
                "memberCreator": {"id": "7", "fullName": "Trello user"},
            }

        def respond(method: str, path: str, params: object) -> object:
            self.assertEqual(("GET", "/cards/issue-1/actions"), (method, path))
            values = cast(Mapping[str, object], params)
            self.assertEqual("commentCard", values["filter"])
            return [action(index) for index in range(1000)] if "before" not in values else [action(1000)]

        provider, http = self.provider(respond)
        comments = list(provider.iter_comments("board", ISSUE))

        self.assertEqual(1001, len(comments))
        self.assertEqual("a-0000", comments[0].comment_id)
        self.assertEqual("a-1000", comments[-1].comment_id)
        second_params = cast(Mapping[str, object], http.calls[1][2])
        self.assertEqual("a-0999", second_params["before"])

    def test_create_sends_comment_text_as_a_parameter(self) -> None:
        def respond(method: str, path: str, value: object) -> object:
            body, params = cast(tuple[object, Mapping[str, object]], value)
            self.assertEqual(("POST", "/cards/issue-1/actions/comments"), (method, path))
            self.assertIsNone(body)
            self.assertEqual(DRAFT.body, params["text"])
            return {
                "id": "new",
                "type": "commentCard",
                "date": "2026-08-13T12:30:00Z",
                "idMemberCreator": "7",
                "data": {"text": DRAFT.body},
                "memberCreator": {"id": "7", "fullName": "Me"},
            }

        provider, _ = self.provider(respond)
        self.assertEqual("new", provider.create_comment("board", ISSUE, DRAFT).comment_id)

    def test_repeated_before_cursor_fails_instead_of_truncating_the_thread(self) -> None:
        actions = [
            {
                "id": f"a-{index:04d}",
                "type": "commentCard",
                "date": "2026-08-13T12:30:00Z",
                "data": {"text": "reply"},
            }
            for index in range(1000)
        ]

        def respond(method: str, path: str, params: object) -> object:
            del method, path, params
            return actions

        provider, _ = self.provider(respond)

        with self.assertRaises(PaginationError):
            list(provider.iter_comments("board", ISSUE))

    def test_rejects_missing_null_and_blank_action_ids_as_payload_errors(self) -> None:
        for invalid_id in (_MISSING, None, "", "   "):
            action: dict[str, object] = {"type": "commentCard", "data": {"text": "unsafe to cache"}}
            if invalid_id is not _MISSING:
                action["id"] = invalid_id

            def respond(method: str, path: str, params: object, *, row: object = action) -> object:
                del method, path, params
                return [row]

            provider, _ = self.provider(respond)
            with self.subTest(invalid_id=invalid_id), self.assertRaises(PayloadError):
                list(provider.iter_comments("board", ISSUE))


class CapabilityTests(unittest.TestCase):
    def test_all_three_advertise_read_and_create(self) -> None:
        for provider in (PlaneProvider, ShortcutProvider, TrelloProvider):
            with self.subTest(provider=provider.spec.name):
                self.assertTrue(provider.spec.capabilities.read_comments)
                self.assertTrue(provider.spec.capabilities.create_comments)


class CommentTransportEdgeTests(unittest.TestCase):
    def _providers(self) -> list[Provider]:
        plane = PlaneProvider(
            {"workspace": "acme", "project_id": "P1", "base_url": "https://plane.test"},
            {"token": "secret"},
        )
        plane._members_by_project["P1"] = {}
        shortcut = ShortcutProvider({"workflow_id": "10"}, {"token": "secret"})
        shortcut._member_names = {}
        return [
            plane,
            shortcut,
            TrelloProvider({"board_id": "board"}, {"key": "key", "token": "token"}),
        ]

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
        malformed: dict[str, object] = {
            "plane": {},
            "shortcut": {"unexpected": []},
            "trello": {"unexpected": []},
        }
        for provider in self._providers():
            with self.subTest(provider=provider.spec.name):
                self.addCleanup(provider.close)

                def handler(request: httpx.Request, *, name: str = provider.spec.name) -> httpx.Response:
                    return httpx.Response(200, json=malformed[name])

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

                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(201, json={"unexpected": "shape"})

                _bind_transport(provider, "https://provider.test", handler)
                with self.assertRaises(PayloadError):
                    provider.create_comment("P1", ISSUE, DRAFT)


if __name__ == "__main__":
    unittest.main()
