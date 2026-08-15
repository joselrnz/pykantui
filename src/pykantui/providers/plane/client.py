"""Authenticated transport and typed Plane API operations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Self

from pykantui.api import (
    JsonClient,
    JsonHttp,
    JsonObject,
    ResponseCache,
    expect_object,
    page_objects_by_cursor,
    parse_json,
)

from . import routes
from .schemas import (
    CommentsPageWire,
    CommentWire,
    LabelWire,
    MembersPageWire,
    MembersWire,
    MemberWire,
    ProjectWire,
    StateWire,
    WorkItemWire,
)


class PlaneClient(JsonHttp):
    """Plane X-API-Key transport."""

    @classmethod
    def connect(cls, base_url: str, token: str, *, cache: ResponseCache | None = None) -> Self:
        """Create a pooled authenticated Plane transport."""
        return cls.with_header_key(base_url.rstrip("/"), "X-API-Key", token, cache=cache)


class PlaneApi:
    """Typed Plane operations scoped to one workspace."""

    def __init__(self, client: JsonClient, workspace: str) -> None:
        self._client = client
        self._workspace = workspace

    def verify_workspace(self) -> None:
        """Check that the token can reach the configured workspace."""
        self._client.get(routes.projects(self._workspace), {"per_page": 1})

    def projects(self) -> Iterator[ProjectWire]:
        """Yield every project across cursor pages."""
        def fetch(cursor: str | None) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.projects(self._workspace), {"per_page": 100, "cursor": cursor}
                ),
                context="Plane projects page",
            )

        for row in page_objects_by_cursor(fetch):
            yield ProjectWire.model_validate(row)

    def states(self, project_id: str, *, ttl: float) -> Iterator[StateWire]:
        """Yield every workflow state in a project."""
        def fetch(cursor: str | None) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.project_resource(self._workspace, project_id, "states"),
                    {"per_page": 100, "cursor": cursor},
                    ttl=ttl,
                    label="states",
                ),
                context="Plane states page",
            )

        for row in page_objects_by_cursor(fetch):
            yield StateWire.model_validate(row)

    def work_items(self, project_id: str, *, ttl: float) -> Iterator[WorkItemWire]:
        """Yield every work item in a project."""
        def fetch(cursor: str | None) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.project_resource(self._workspace, project_id, "work-items"),
                    {"per_page": 100, "cursor": cursor},
                    ttl=ttl,
                    label="work items",
                ),
                context="Plane work-items page",
            )

        for row in page_objects_by_cursor(fetch):
            yield WorkItemWire.model_validate(row)

    def labels(self, project_id: str, *, ttl: float) -> Iterator[LabelWire]:
        """Yield every project label."""
        def fetch(cursor: str | None) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.project_resource(self._workspace, project_id, "labels"),
                    {"per_page": 100, "cursor": cursor},
                    ttl=ttl,
                    label="labels",
                ),
                context="Plane labels page",
            )

        for row in page_objects_by_cursor(fetch):
            yield LabelWire.model_validate(row)

    def project(self, project_id: str, *, ttl: float) -> ProjectWire:
        """Return one project."""
        return parse_json(
            self._client.get(
                routes.project(self._workspace, project_id), ttl=ttl, label="project"
            ),
            ProjectWire,
        )

    def members(self, project_id: str, *, ttl: float) -> list[MemberWire]:
        """Return members from Plane's bare-list or paged response shapes."""
        response = self._client.get(
            routes.project_resource(self._workspace, project_id, "members"),
            ttl=ttl,
            label="members",
        )
        if isinstance(response, list):
            return parse_json(response, MembersWire).root
        return parse_json(response, MembersPageWire).results

    def work_item(self, project_id: str, issue_id: str) -> WorkItemWire:
        """Return one work item without caching."""
        return parse_json(
            self._client.get(routes.work_item(self._workspace, project_id, issue_id)), WorkItemWire
        )

    def create_work_item(self, project_id: str, body: JsonObject) -> WorkItemWire:
        """Create and validate one work item."""
        return parse_json(
            self._client.post(
                routes.project_resource(self._workspace, project_id, "work-items"), body
            ),
            WorkItemWire,
        )

    def update_work_item(self, project_id: str, issue_id: str, body: JsonObject) -> None:
        """Update one work item."""
        self._client.patch(routes.work_item(self._workspace, project_id, issue_id), body)

    def comments(self, project_id: str, issue_id: str) -> Iterator[CommentWire]:
        """Yield every current work-item comment across cursor pages."""

        def fetch(cursor: str | None) -> JsonObject:
            response = expect_object(
                self._client.get(
                    routes.comments(self._workspace, project_id, issue_id),
                    {"per_page": 100, "cursor": cursor, "order_by": "created_at"},
                ),
                context="Plane comments page",
            )
            parse_json(response, CommentsPageWire)
            return response

        for row in page_objects_by_cursor(fetch):
            yield parse_json(row, CommentWire)

    def create_comment(self, project_id: str, issue_id: str, body: JsonObject) -> CommentWire:
        """Append one Plane work-item comment without automatic replay."""

        return parse_json(
            self._client.post(routes.comments(self._workspace, project_id, issue_id), body),
            CommentWire,
        )


__all__ = ["PlaneApi", "PlaneClient"]
