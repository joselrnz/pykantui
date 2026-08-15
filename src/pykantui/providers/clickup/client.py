"""Authenticated transport and typed ClickUp API operations."""

from __future__ import annotations

from typing import Self

from pykantui.api import JsonClient, JsonHttp, JsonObject, ResponseCache, parse_json

from . import routes
from .schemas import (
    CommentsEnvelope,
    CommentWire,
    CreatedCommentWire,
    CustomItemTypesEnvelope,
    CustomItemTypeWire,
    FoldersEnvelope,
    ListsEnvelope,
    ListWire,
    MembersEnvelope,
    SpacesEnvelope,
    SpaceWire,
    TasksEnvelope,
    TaskWire,
    TeamsEnvelope,
    TeamWire,
    UserEnvelope,
    UserWire,
)


class ClickUpClient(JsonHttp):
    """ClickUp bare-Authorization transport."""

    @classmethod
    def connect(cls, base_url: str, token: str, *, cache: ResponseCache | None = None) -> Self:
        """Create a pooled authenticated ClickUp transport."""
        return cls.with_header_key(base_url.rstrip("/"), "Authorization", token, cache=cache)


class ClickUpApi:
    """Typed ClickUp operations over an injectable JSON client."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def current_user(self) -> UserWire:
        """Return the authenticated ClickUp user."""
        return parse_json(self._client.get(routes.CURRENT_USER), UserEnvelope).user

    def teams(self) -> list[TeamWire]:
        """Return workspaces/teams visible to the account."""
        return list(parse_json(self._client.get(routes.TEAMS), TeamsEnvelope).teams)

    def spaces(self, team_id: str, *, archived: bool = False) -> list[SpaceWire]:
        """Return spaces inside a workspace/team."""
        return list(
            parse_json(
                self._client.get(routes.spaces(team_id), {"archived": str(archived).lower()}),
                SpacesEnvelope,
            ).spaces
        )

    def space_lists(self, space_id: str) -> list[ListWire]:
        """Return lists attached directly to a space."""
        return parse_json(
            self._client.get(routes.space_lists(space_id), {"archived": "false"}), ListsEnvelope
        ).lists

    def folders(self, space_id: str) -> FoldersEnvelope:
        """Return folders and their embedded lists."""
        return parse_json(
            self._client.get(routes.folders(space_id), {"archived": "false"}), FoldersEnvelope
        )

    def list_(self, list_id: str) -> ListWire:
        """Return one ClickUp list."""
        return parse_json(self._client.get(routes.list_(list_id)), ListWire)

    def tasks(self, list_id: str, page: int) -> list[TaskWire]:
        """Return one zero-based task page."""
        return parse_json(
            self._client.get(
                routes.tasks(list_id),
                {"page": page, "subtasks": "true", "include_closed": "true"},
            ),
            TasksEnvelope,
        ).tasks

    def task(self, task_id: str) -> TaskWire:
        """Return one task."""
        return parse_json(self._client.get(routes.task(task_id)), TaskWire)

    def comments(
        self,
        task_id: str,
        *,
        start: str | None = None,
        start_id: str | None = None,
    ) -> list[CommentWire]:
        """Return one reverse-chronological task-comment page."""
        return parse_json(
            self._client.get(
                routes.task_comments(task_id),
                {"start": start, "start_id": start_id},
            ),
            CommentsEnvelope,
        ).comments

    def create_comment(self, task_id: str, payload: JsonObject) -> CreatedCommentWire:
        """Create one comment; POST is deliberately never retried."""
        return parse_json(
            self._client.post(routes.task_comments(task_id), payload),
            CreatedCommentWire,
        )

    def comment_replies(self, comment_id: str) -> list[CommentWire]:
        """Return the replies beneath one comment."""
        return parse_json(
            self._client.get(
                routes.comment_replies(comment_id),
            ),
            CommentsEnvelope,
        ).comments

    def custom_item_types(self, team_id: str, *, ttl: float) -> list[CustomItemTypeWire]:
        """Return the workspace directory used to resolve task type ids."""
        return list(
            parse_json(
                self._client.get(
                    routes.custom_item_types(team_id),
                    ttl=ttl,
                    label="custom item types",
                ),
                CustomItemTypesEnvelope,
            ).custom_items
        )

    def create_task(self, list_id: str, payload: JsonObject) -> TaskWire:
        """Create and validate one task."""
        return parse_json(self._client.post(routes.tasks(list_id), payload), TaskWire)

    def update_task(self, task_id: str, payload: JsonObject) -> None:
        """Update one task."""
        self._client.put(routes.task(task_id), payload)

    def members(self, list_id: str) -> list[UserWire]:
        """Return users assignable to a list."""
        return parse_json(self._client.get(routes.members(list_id)), MembersEnvelope).members

    def add_tag(self, task_id: str, label: str) -> None:
        """Attach one tag to a task."""
        self._client.post(routes.task_tag(task_id, label))

    def remove_tag(self, task_id: str, label: str) -> None:
        """Detach one tag from a task."""
        self._client.delete(routes.task_tag(task_id, label))


__all__ = ["ClickUpApi", "ClickUpClient"]
