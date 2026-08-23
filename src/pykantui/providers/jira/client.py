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
    page_by_offset,
    page_objects_by_offset,
    page_objects_by_token,
    parse_json,
)

from . import routes
from .enums import JiraFieldType, JiraSprintState, enum_values
from .schemas import (
    BoardConfigurationWire,
    BoardWire,
    CommentsPageWire,
    CommentWire,
    ComponentWire,
    CreatedIssueWire,
    CreateFieldMetadataWire,
    EditMetadataWire,
    FieldWire,
    IssueTypesWire,
    IssueWire,
    PriorityWire,
    ProjectStatusesWire,
    ProjectWire,
    SprintWire,
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

    def create_fields(
        self,
        project_id_or_key: str,
        issue_type_id: str,
        *,
        ttl: float,
    ) -> Iterator[CreateFieldMetadataWire]:
        """Yield every create-screen field for one project and issue type."""

        def fetch(start: int, size: int) -> JsonObject:
            response = expect_object(
                self._client.get(
                    routes.create_field_metadata(project_id_or_key, issue_type_id),
                    {"startAt": start, "maxResults": min(size, 200)},
                    ttl=ttl,
                    label="create fields",
                ),
                context="Jira create-field metadata response",
            )
            # Atlassian's current schema documents ``fields`` and ``results``.
            # Normalize either variant before paging.
            values = response.get("results")
            if values is None:
                values = response.get("fields")
            return {**response, "values": values or []}

        for row in page_objects_by_offset(fetch, page_size=200, items_key="values"):
            yield CreateFieldMetadataWire.model_validate(row)

    def edit_metadata(self, issue_id_or_key: str) -> EditMetadataWire:
        """Return fields currently editable on one issue."""

        return parse_json(
            self._client.get(routes.edit_metadata(issue_id_or_key)),
            EditMetadataWire,
        )

    def fields(
        self,
        *,
        ttl: float,
        field_types: tuple[JiraFieldType | str, ...] = (),
        project_ids: tuple[str, ...] = (),
        query: str = "",
    ) -> Iterator[FieldWire]:
        """Yield visible system and custom fields from Jira field search."""

        normalized_types = enum_values(JiraFieldType, field_types, label="field type")

        def fetch(start: int, size: int) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.FIELDS,
                    {
                        "startAt": start,
                        "maxResults": size,
                        "type": normalized_types or None,
                        "projectIds": project_ids or None,
                        "query": query or None,
                    },
                    ttl=ttl,
                    label="fields",
                ),
                context="Jira field-search response",
            )

        for row in page_objects_by_offset(fetch, items_key="values"):
            yield FieldWire.model_validate(row)

    def priorities(
        self,
        *,
        ttl: float,
        project_ids: tuple[str, ...] = (),
    ) -> Iterator[PriorityWire]:
        """Yield visible Jira priorities, optionally filtered by project."""

        def fetch(start: int, size: int) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.PRIORITIES,
                    {
                        "startAt": start,
                        "maxResults": size,
                        "projectId": project_ids or None,
                    },
                    ttl=ttl,
                    label="priorities",
                ),
                context="Jira priority-search response",
            )

        for row in page_objects_by_offset(fetch, items_key="values"):
            yield PriorityWire.model_validate(row)

    def labels(self, *, ttl: float) -> Iterator[str]:
        """Yield every visible Jira label across offset pages."""

        def fetch(start: int, size: int) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.LABELS,
                    {"startAt": start, "maxResults": size},
                    ttl=ttl,
                    label="labels",
                ),
                context="Jira labels response",
            )

        for value in page_by_offset(fetch, items_key="values"):
            if not isinstance(value, str):
                raise ValueError("Jira labels response contained a non-string label")
            yield value

    def sprints(
        self,
        board_id: str,
        *,
        ttl: float,
        states: tuple[JiraSprintState | str, ...] = (),
    ) -> Iterator[SprintWire]:
        """Yield board sprints, optionally restricted to Jira sprint states."""

        normalized_states = enum_values(JiraSprintState, states, label="sprint state")

        def fetch(start: int, size: int) -> JsonObject:
            return expect_object(
                self._client.get(
                    routes.board_sprints(board_id),
                    {
                        "startAt": start,
                        "maxResults": size,
                        "state": normalized_states or None,
                    },
                    ttl=ttl,
                    label="sprints",
                ),
                context="Jira board-sprints response",
            )

        for row in page_objects_by_offset(fetch, items_key="values"):
            yield SprintWire.model_validate(row)

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
