"""Authenticated transport and typed Forgejo API operations."""

from __future__ import annotations

from typing import Self

from pykantui.api import JsonClient, JsonHttp, JsonObject, ResponseCache, parse_json

from . import routes
from .schemas import (
    CommentsWire,
    CommentWire,
    IssuesWire,
    IssueWire,
    LabelsWire,
    LabelWire,
    RepositoriesWire,
    RepositoryWire,
    UserWire,
)

API_PATH = "/api/v1"


def api_base_url(value: str) -> str:
    """Accept either an instance URL or its explicit ``/api/v1`` URL."""
    base = value.strip().rstrip("/")
    return base if base.casefold().endswith(API_PATH) else f"{base}{API_PATH}"


class ForgejoClient(JsonHttp):
    @classmethod
    def connect(
        cls,
        base_url: str,
        token: str,
        *,
        cache: ResponseCache | None = None,
    ) -> Self:
        return cls(
            api_base_url(base_url),
            headers={"Authorization": f"token {token}"},
            cache=cache,
            sensitive_values=(token,),
        )


class ForgejoApi:
    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def current_user(self) -> UserWire:
        return parse_json(self._client.get(routes.CURRENT_USER), UserWire)

    def repositories(self, page: int) -> list[RepositoryWire]:
        return parse_json(
            self._client.get(routes.REPOSITORIES, {"page": page, "limit": 50, "order_by": "name"}),
            RepositoriesWire,
        ).root

    def labels(self, repository: str, page: int) -> list[LabelWire]:
        return parse_json(
            self._client.get(routes.labels(repository), {"page": page, "limit": 50}),
            LabelsWire,
        ).root

    def issues(self, repository: str, page: int) -> list[IssueWire]:
        return parse_json(
            self._client.get(
                routes.issues(repository),
                {"page": page, "limit": 50, "state": "all", "type": "issues"},
            ),
            IssuesWire,
        ).root

    def issue(self, repository: str, number: object) -> IssueWire:
        return parse_json(self._client.get(routes.issue(repository, number)), IssueWire)

    def comments(self, repository: str, number: object, page: int) -> list[CommentWire]:
        return parse_json(
            self._client.get(routes.issue_comments(repository, number), {"page": page, "limit": 50}),
            CommentsWire,
        ).root

    def create_comment(self, repository: str, number: object, payload: JsonObject) -> CommentWire:
        return parse_json(self._client.post(routes.issue_comments(repository, number), payload), CommentWire)

    def create_issue(self, repository: str, payload: JsonObject) -> IssueWire:
        return parse_json(self._client.post(routes.issues(repository), payload), IssueWire)

    def update_issue(self, repository: str, number: object, payload: JsonObject) -> None:
        self._client.patch(routes.issue(repository, number), payload)

    def replace_labels(self, repository: str, number: object, labels: list[str]) -> None:
        self._client.put(routes.issue_labels(repository, number), {"labels": labels})


__all__ = ["API_PATH", "ForgejoApi", "ForgejoClient", "api_base_url"]
