"""Authenticated transport and typed Jira Cloud API operations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Self

from pykantui.api import (
    JsonClient,
    JsonHttp,
    JsonObject,
    ResponseCache,
    expect_object,
    page_objects_by_offset,
    page_objects_by_token,
    parse_json,
)

from . import routes
from .schemas import (
    BoardConfigurationWire,
    BoardWire,
    CommentsPageWire,
    CommentWire,
    ComponentWire,
    CreatedIssueWire,
    IssueTypesWire,
    IssueWire,
    ProjectStatusesWire,
    ProjectWire,
    TransitionsWire,
    UserWire,
)


class JiraClient(JsonHttp):
    """Jira Cloud preemptive-basic-auth transport."""

    @classmethod
    def connect(
        cls,
        base_url: str,
        email: str,
        token: str,
        *,
        cache: ResponseCache | None = None,
    ) -> Self:
        """Create a pooled authenticated Jira Cloud transport."""
        return cls.with_basic_auth(base_url.rstrip("/"), email, token, cache=cache)


class JiraApi:
    """Typed Jira operations over an injectable JSON client."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def current_user(self) -> UserWire:
        """Return the authenticated Jira account."""
        return parse_json(self._client.get(routes.CURRENT_USER), UserWire)

    def projects(self, *, ttl: float) -> Iterator[ProjectWire]:
        """Yield every visible project across offset pages."""
        def fetch(start: int, size: int) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.PROJECTS,
                    {"startAt": start, "maxResults": size},
                    ttl=ttl,
                    label="projects",
                ),
                context="Jira projects response",
            )

        for row in page_objects_by_offset(fetch, items_key="values"):
            yield ProjectWire.model_validate(row)

    def boards(self) -> Iterator[BoardWire]:
        """Yield every agile board across offset pages."""
        def fetch(start: int, size: int) -> JsonObject:
            return expect_object(
                self._client.get(routes.BOARDS, {"startAt": start, "maxResults": size}),
                context="Jira boards response",
            )

        for row in page_objects_by_offset(fetch, items_key="values"):
            yield BoardWire.model_validate(row)

    def board_configuration(self, board_id: str, *, ttl: float) -> BoardConfigurationWire:
        """Return one agile-board configuration."""
        return parse_json(
            self._client.get(
                routes.board_configuration(board_id), ttl=ttl, label="board"
            ),
            BoardConfigurationWire,
        )

    def project_statuses(self, project_key: str, *, ttl: float) -> ProjectStatusesWire:
        """Return statuses grouped by issue type for a project."""
        return parse_json(
            self._client.get(
                routes.project_statuses(project_key), ttl=ttl, label="statuses"
            ),
            ProjectStatusesWire,
        )

    def issues(self, jql: str, *, fields: str, ttl: float) -> Iterator[IssueWire]:
        """Yield every issue from Jira's token-paged JQL endpoint."""
        def fetch(token: str | None) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.SEARCH,
                    {"jql": jql, "maxResults": 100, "fields": fields, "nextPageToken": token},
                    ttl=ttl,
                    label="issues",
                ),
                context="Jira issue-search response",
            )

        for row in page_objects_by_token(fetch, items_key="issues"):
            yield IssueWire.model_validate(row)

    def issue(self, key: str, *, fields: str) -> IssueWire:
        """Return one Jira issue without caching."""
        return parse_json(
            self._client.get(routes.issue(key), {"fields": fields}), IssueWire
        )

    def transition(self, key: str, transition_id: str) -> None:
        """Move an issue through one workflow transition."""
        self._client.post(routes.transitions(key), {"transition": {"id": transition_id}})

    def issue_types(self, project_id: str, *, ttl: float) -> IssueTypesWire:
        """Return issue types accepted by a project."""
        return parse_json(
            self._client.get(
                routes.issue_types(project_id), ttl=ttl, label="issue types"
            ),
            IssueTypesWire,
        )

    def components(self, project_id_or_key: str, *, ttl: float) -> Iterator[ComponentWire]:
        """Yield all components configured for a Jira project."""

        def fetch(start: int, size: int) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.project_components(project_id_or_key),
                    {"startAt": start, "maxResults": size},
                    ttl=ttl,
                    label="components",
                ),
                context="Jira project-components response",
            )

        for row in page_objects_by_offset(fetch, items_key="values"):
            yield ComponentWire.model_validate(row)

    def create_issue(self, fields: JsonObject) -> CreatedIssueWire:
        """Create a Jira issue using v2 string-description semantics."""
        return parse_json(
            self._client.post(routes.CREATE_ISSUE, {"fields": fields}), CreatedIssueWire
        )

    def update_issue(self, key: str, fields: JsonObject) -> None:
        """Update Jira fields using v2 string-description semantics."""
        self._client.put(routes.issue_update(key), {"fields": fields})

    def assignable_users(self, issue_key: str, query: str) -> list[UserWire]:
        """Return matching users assignable to an issue."""
        response = self._client.get(
            routes.ASSIGNABLE_USERS,
            {"issueKey": issue_key, "query": query, "maxResults": 20},
        )
        if not isinstance(response, list):
            return []
        return [UserWire.model_validate(item) for item in response]

    def transitions(self, issue_key: str) -> TransitionsWire:
        """Return workflow transitions currently available to an issue."""
        return parse_json(self._client.get(routes.transitions(issue_key)), TransitionsWire)

    def comments(self, issue_key: str) -> Iterator[CommentWire]:
        """Yield every visible comment from Jira's offset pages."""

        def fetch(start: int, size: int) -> JsonObject:
            response = expect_object(
                self._client.get(
                    routes.comments(issue_key),
                    {"startAt": start, "maxResults": min(size, 100), "orderBy": "created"},
                ),
                context="Jira comments response",
            )
            parse_json(response, CommentsPageWire)
            return response

        for row in page_objects_by_offset(fetch, page_size=100, items_key="comments"):
            yield parse_json(row, CommentWire)

    def create_comment(self, issue_key: str, body: JsonObject) -> CommentWire:
        """Append one Jira comment without automatic replay."""

        return parse_json(
            self._client.post(routes.comments(issue_key), {"body": body}),
            CommentWire,
        )


__all__ = ["JiraApi", "JiraClient"]
