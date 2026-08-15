"""Authenticated transport and typed Linear GraphQL operations."""

from collections.abc import Iterator
from typing import Self

from pykantui.api import JsonClient, JsonHttp, JsonObject, PaginationError, ResponseCache, parse_json

from . import operations
from .schemas import (
    CommentCreateDataWire,
    CommentsDataWire,
    CommentWire,
    IssueCreateDataWire,
    IssueDataWire,
    IssueWire,
    LabelsDataWire,
    LabelWire,
    StateWire,
    TeamDataWire,
    TeamsDataWire,
    TeamWire,
    UsersDataWire,
    UserWire,
    ViewerDataWire,
)


class LinearClient(JsonHttp):
    """Linear personal keys are bare ``Authorization`` values."""

    @classmethod
    def connect(cls, base_url: str, token: str, *, cache: ResponseCache | None = None) -> Self:
        return cls.with_header_key(base_url, "Authorization", token, cache=cache)


class LinearApi:
    """Typed Linear operations over an injectable GraphQL transport."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def viewer(self) -> UserWire:
        """Return the authenticated Linear user."""
        response = self._client.graphql("query { viewer { id name displayName email } }")
        return parse_json(response, ViewerDataWire).viewer

    def teams(self) -> Iterator[TeamWire]:
        """Yield all teams across Relay cursor pages."""
        cursor: str | None = None
        while True:
            response = self._client.graphql(operations.TEAMS_QUERY, {"cursor": cursor})
            connection = parse_json(response, TeamsDataWire).teams
            yield from connection.nodes
            if not connection.pageInfo.hasNextPage:
                return
            cursor = connection.pageInfo.endCursor

    def issue(self, issue_id: str) -> IssueWire | None:
        """Return one issue by UUID or human identifier."""
        response = self._client.graphql(operations.ONE_ISSUE_QUERY, {"id": issue_id})
        return parse_json(response, IssueDataWire).issue

    def states(self, team_id: str) -> list[StateWire]:
        """Return workflow states for one team."""
        response = self._client.graphql(operations.STATES_QUERY, {"team": team_id})
        return parse_json(response, TeamDataWire).team.states.nodes

    def issues(self, team_id: str) -> Iterator[IssueWire]:
        """Yield every issue for one team."""
        cursor: str | None = None
        while True:
            response = self._client.graphql(
                operations.ISSUES_QUERY,
                {"team": team_id, "cursor": cursor},
            )
            connection = parse_json(response, TeamDataWire).team.issues
            yield from connection.nodes
            if not connection.pageInfo.hasNextPage:
                return
            cursor = connection.pageInfo.endCursor

    def create_issue(self, payload: JsonObject) -> IssueWire | None:
        """Create an issue and return the mutation's issue record."""
        response = self._client.graphql(operations.CREATE_MUTATION, {"input": payload})
        return parse_json(response, IssueCreateDataWire).issueCreate.issue

    def move_issue(self, issue_id: str, state_id: str) -> None:
        """Move an issue to one workflow state."""
        self._client.graphql(
            operations.MOVE_MUTATION,
            {"id": issue_id, "state": state_id},
        )

    def update_issue(self, issue_id: str, payload: JsonObject) -> None:
        """Update writable fields on one issue."""
        self._client.graphql(
            operations.UPDATE_MUTATION,
            {"id": issue_id, "input": payload},
        )

    def users(self) -> list[UserWire]:
        """Return users visible in the workspace."""
        response = self._client.graphql(operations.USERS_QUERY)
        return parse_json(response, UsersDataWire).users.nodes

    def labels(self) -> list[LabelWire]:
        """Return issue labels visible in the workspace."""
        response = self._client.graphql(operations.LABELS_QUERY)
        return list(parse_json(response, LabelsDataWire).issueLabels.nodes)

    def comments(self, issue_id: str) -> Iterator[CommentWire]:
        """Yield a complete Linear comment connection."""

        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(1000):
            response = self._client.graphql(
                operations.COMMENTS_QUERY,
                {"id": issue_id, "cursor": cursor},
            )
            issue = parse_json(response, CommentsDataWire).issue
            yield from issue.comments.nodes
            if not issue.comments.pageInfo.hasNextPage:
                return
            cursor = issue.comments.pageInfo.endCursor
            if not cursor:
                raise PaginationError("Linear omitted the next comment cursor")
            if cursor in seen:
                raise PaginationError("Linear repeated a comment cursor")
            seen.add(cursor)
        raise PaginationError("Linear comment pagination exceeded 1000 pages")

    def create_comment(self, issue_id: str, body: str) -> CommentWire | None:
        """Append a Markdown comment and return Linear's canonical record."""

        response = self._client.graphql(
            operations.CREATE_COMMENT_MUTATION,
            {"input": {"issueId": issue_id, "body": body}},
        )
        return parse_json(response, CommentCreateDataWire).commentCreate.comment


__all__ = ["LinearApi", "LinearClient"]
