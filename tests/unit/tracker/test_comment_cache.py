"""Provider-neutral, issue-scoped comment cache behavior."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path

import httpx

from pykantui.api import ResponseCache
from pykantui.providers.asana import AsanaProvider
from pykantui.providers.asana.client import AsanaClient
from pykantui.tracker.base import Provider
from pykantui.tracker.models import (
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.spec import Capabilities, ProviderSpec


class CountingCommentProvider(Provider):
    """Small provider used to observe the shared normalized cache."""

    spec = ProviderSpec(
        name="counting-comments",
        label="Counting comments",
        capabilities=Capabilities(read_comments=True),
    )

    def __init__(self) -> None:
        super().__init__({}, {})
        self.reads: list[str] = []
        self.revisions: dict[str, int] = {}

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me")

    def list_projects(self) -> list[RemoteProject]:
        return []

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return []

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(())

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        del project_id
        self.reads.append(issue.issue_id)
        revision = self.revisions.get(issue.issue_id, 1)
        yield RemoteComment(
            comment_id=f"{issue.issue_id}-{revision}",
            issue_id=issue.issue_id,
            body=f"revision {revision}",
        )


class CommentCacheScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_invalidating_issue_a_preserves_issue_b(self) -> None:
        provider = CountingCommentProvider()
        provider.use_cache(ResponseCache(self.root).scope(provider.spec.name, "workspace"))
        issue_a = RemoteIssue(issue_id="A", key="CARD-A")
        issue_b = RemoteIssue(issue_id="B", key="CARD-B")

        self.assertEqual("A-1", provider.comments("project", issue_a)[0].comment_id)
        self.assertEqual("B-1", provider.comments("project", issue_b)[0].comment_id)
        provider.revisions.update(A=2, B=2)

        provider.invalidate_comment_cache("project", "A")

        self.assertEqual("A-2", provider.comments("project", issue_a)[0].comment_id)
        self.assertEqual("B-1", provider.comments("project", issue_b)[0].comment_id)
        self.assertEqual(["A", "B", "A"], provider.reads)

    def test_explicit_refresh_replaces_only_the_selected_thread(self) -> None:
        provider = CountingCommentProvider()
        provider.use_cache(ResponseCache(self.root).scope(provider.spec.name, "workspace"))
        issue_a = RemoteIssue(issue_id="A", key="CARD-A")
        issue_b = RemoteIssue(issue_id="B", key="CARD-B")
        provider.comments("project", issue_a)
        provider.comments("project", issue_b)
        provider.revisions.update(A=2, B=2)

        refreshed = provider.comments("project", issue_a, refresh=True)
        preserved = provider.comments("project", issue_b)

        self.assertEqual("A-2", refreshed[0].comment_id)
        self.assertEqual("B-1", preserved[0].comment_id)
        self.assertEqual(["A", "B", "A"], provider.reads)

    def test_adapter_pages_do_not_create_a_second_comment_cache_layer(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            offset = request.url.params.get("offset")
            return httpx.Response(
                200,
                json={
                    "data": [{
                        "gid": "story-1" if offset is None else "story-2",
                        "resource_subtype": "comment_added",
                        "type": "comment",
                        "text": "first" if offset is None else "second",
                        "created_at": "2026-08-13T12:00:00Z",
                    }],
                    "next_page": {"offset": "page-2"} if offset is None else None,
                },
                request=request,
            )

        cache = ResponseCache(self.root).scope("asana", "workspace")
        raw = httpx.Client(
            base_url="https://app.asana.test/api/1.0",
            transport=httpx.MockTransport(handler),
        )
        provider = AsanaProvider({"project_id": "project"}, {"token": "secret"})
        provider._http = AsanaClient(
            "https://app.asana.test/api/1.0",
            client=raw,
            cache=cache,
        )
        provider.use_cache(cache)
        self.addCleanup(provider.close)

        issue = RemoteIssue(issue_id="task-1")
        self.assertEqual(2, len(provider.comments("project", issue)))
        self.assertEqual(2, len(provider.comments("project", issue)))

        self.assertEqual(2, len(requests))
        self.assertEqual(
            1,
            len(list(cache.directory().glob("comments-*.json"))),
            "only Provider.comments should persist the normalized thread",
        )


if __name__ == "__main__":
    unittest.main()
