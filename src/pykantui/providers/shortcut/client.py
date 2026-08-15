"""Authenticated transport and typed Shortcut API operations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Self

from pykantui.api import JsonClient, JsonHttp, JsonObject, QueryParams, ResponseCache, parse_json

from . import routes
from .schemas import (
    CommentsWire,
    CommentWire,
    MembersWire,
    MemberWire,
    SearchPageWire,
    StoryWire,
    WorkflowsWire,
    WorkflowWire,
)


class ShortcutClient(JsonHttp):
    """Shortcut header-token transport."""

    @classmethod
    def connect(cls, base_url: str, token: str, *, cache: ResponseCache | None = None) -> Self:
        """Create a pooled authenticated Shortcut transport."""
        return cls.with_header_key(base_url.rstrip("/"), "Shortcut-Token", token, cache=cache)


class ShortcutApi:
    """Typed Shortcut operations over an injectable JSON client."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def current_member(self) -> MemberWire:
        """Return the authenticated member."""
        return parse_json(self._client.get(routes.CURRENT_MEMBER), MemberWire)

    def workflows(self) -> list[WorkflowWire]:
        """Return every workflow visible to the account."""
        return parse_json(self._client.get(routes.WORKFLOWS), WorkflowsWire).root

    def stories(self, query: str, *, page_size: int) -> Iterator[StoryWire]:
        """Yield search results across provider-supplied continuation paths."""
        path = routes.SEARCH_STORIES
        params: QueryParams | None = {"query": query, "page_size": page_size, "detail": "full"}
        for _ in range(1000):
            page = parse_json(self._client.get(path, params), SearchPageWire)
            yield from page.data
            if not page.next:
                return
            path, params = routes.search_continuation(page.next), None

    def members(self, *, ttl: float) -> list[MemberWire]:
        """Return assignable workspace members."""
        return parse_json(
            self._client.get(routes.MEMBERS, ttl=ttl, label="members"), MembersWire
        ).root

    def story(self, story_id: str) -> StoryWire:
        """Return one story."""
        return parse_json(self._client.get(routes.story(story_id)), StoryWire)

    def create_story(self, payload: JsonObject) -> StoryWire:
        """Create and validate one story."""
        return parse_json(self._client.post(routes.STORIES, payload), StoryWire)

    def update_story(self, story_id: str, payload: JsonObject) -> None:
        """Update one story."""
        self._client.put(routes.story(story_id), payload)

    def comments(self, story_id: str) -> list[CommentWire]:
        """Return Shortcut's complete story-comment array."""

        return parse_json(self._client.get(routes.comments(story_id)), CommentsWire).root

    def create_comment(self, story_id: str, text: str) -> CommentWire:
        """Append a story comment while leaving identity/timestamps to Shortcut."""

        return parse_json(
            self._client.post(routes.comments(story_id), {"text": text}),
            CommentWire,
        )


__all__ = ["ShortcutApi", "ShortcutClient"]
