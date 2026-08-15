"""Authenticated transport and typed Asana API operations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Self

from pykantui.api import (
    JsonClient,
    JsonHttp,
    JsonObject,
    QueryParams,
    ResponseCache,
    page_by_next_cursor,
    parse_json,
)

from . import routes
from .schemas import (
    ProjectEnvelope,
    ProjectPage,
    ProjectWire,
    SectionPage,
    SectionWire,
    StoryEnvelope,
    StoryPage,
    StoryWire,
    TaskEnvelope,
    TaskPage,
    TaskWire,
    UserEnvelope,
    UserPage,
    UserWire,
    WorkspacePage,
)


class AsanaClient(JsonHttp):
    """Asana bearer-token transport."""

    @classmethod
    def connect(cls, base_url: str, token: str, *, cache: ResponseCache | None = None) -> Self:
        """Create a pooled authenticated Asana transport."""
        return cls.with_bearer(base_url.rstrip("/"), token, cache=cache)


class AsanaApi:
    """Typed Asana operations over an injectable JSON client."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def current_user(self) -> UserWire:
        """Return the authenticated user."""
        return parse_json(self._client.get(routes.CURRENT_USER), UserEnvelope).data

    def workspaces(self, *, ttl: float) -> list[UserWire]:
        """Return workspaces visible to the user."""
        page = parse_json(
            self._client.get(routes.WORKSPACES, {"opt_fields": "name"}, ttl=ttl), WorkspacePage
        )
        return [UserWire(gid=item.gid, name=item.name) for item in page.data]

    def projects(self, params: QueryParams) -> Iterator[ProjectWire]:
        """Yield every project across Asana continuation pages."""
        cursor: str | None = None
        while True:
            page = parse_json(
                self._client.get(routes.PROJECTS, {**params, "limit": 100, "offset": cursor}), ProjectPage
            )
            yield from page.data
            cursor = page.next_page.offset if page.next_page and page.next_page.offset else None
            if cursor is None:
                return

    def sections(self, project_id: str) -> Iterator[SectionWire]:
        """Yield every section in a project."""
        cursor: str | None = None
        while True:
            page = parse_json(
                self._client.get(
                    routes.sections(project_id),
                    {"opt_fields": "name", "limit": 100, "offset": cursor},
                ),
                SectionPage,
            )
            yield from page.data
            cursor = page.next_page.offset if page.next_page and page.next_page.offset else None
            if cursor is None:
                return

    def tasks(self, project_id: str, *, fields: str) -> Iterator[TaskWire]:
        """Yield every task in a project."""
        cursor: str | None = None
        while True:
            page = parse_json(
                self._client.get(
                    routes.project_tasks(project_id),
                    {"opt_fields": fields, "limit": 100, "offset": cursor},
                ),
                TaskPage,
            )
            yield from page.data
            cursor = page.next_page.offset if page.next_page and page.next_page.offset else None
            if cursor is None:
                return

    def task(self, task_id: str, *, fields: str) -> TaskWire:
        """Return one task."""
        return parse_json(
            self._client.get(routes.task(task_id), {"opt_fields": fields}), TaskEnvelope
        ).data

    def stories(self, task_id: str, *, fields: str) -> Iterator[StoryWire]:
        """Yield every task story across opaque-offset pages."""
        def fetch(cursor: str | None) -> tuple[list[StoryWire], str | None]:
            page = parse_json(
                self._client.get(
                    routes.task_stories(task_id),
                    {"opt_fields": fields, "limit": 100, "offset": cursor},
                ),
                StoryPage,
            )
            next_cursor = page.next_page.offset if page.next_page and page.next_page.offset else None
            return page.data, next_cursor

        yield from page_by_next_cursor(fetch)

    def project(self, project_id: str) -> ProjectWire:
        """Return one project with its workspace reference."""
        return parse_json(
            self._client.get(routes.project(project_id), {"opt_fields": "workspace.gid"}),
            ProjectEnvelope,
        ).data

    def workspace_users(self, workspace_id: str) -> list[UserWire]:
        """Return members available for assignment."""
        return parse_json(
            self._client.get(
                routes.workspace_users(workspace_id), {"opt_fields": "gid,name,email"}
            ),
            UserPage,
        ).data

    def create_task(self, payload: JsonObject) -> TaskWire:
        """Create a task and return its initial response."""
        return parse_json(self._client.post(routes.TASKS, {"data": payload}), TaskEnvelope).data

    def create_story(self, task_id: str, payload: JsonObject, *, fields: str) -> StoryWire:
        """Create a task comment; the transport never retries this POST."""
        return parse_json(
            self._client.post(
                routes.task_stories(task_id),
                {"data": payload},
                {"opt_fields": fields},
            ),
            StoryEnvelope,
        ).data

    def update_task(self, task_id: str, payload: JsonObject) -> None:
        """Update task fields."""
        self._client.put(routes.task(task_id), {"data": payload})

    def add_task_to_section(self, section_id: str, task_id: str) -> None:
        """Place a task in a board section."""
        self._client.post(routes.add_task(section_id), {"data": {"task": task_id}})


__all__ = ["AsanaApi", "AsanaClient"]
