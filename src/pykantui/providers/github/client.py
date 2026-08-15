"""Authenticated transport and typed GitHub API operations."""

from typing import Self

from pykantui.api import JsonClient, JsonHttp, JsonObject, ResponseCache, parse_json

from . import routes
from .schemas import (
    CommentsWire,
    CommentWire,
    IssuesWire,
    IssueTypesWire,
    IssueWire,
    LabelsWire,
    RepositoriesWire,
    RepositoryWire,
    UserWire,
)


class GitHubClient(JsonHttp):
    """GitHub REST client with its required media and version headers."""

    @classmethod
    def connect(
        cls,
        base_url: str,
        token: str,
        *,
        api_version: str,
        cache: ResponseCache | None = None,
    ) -> Self:
        return cls.with_bearer(
            base_url.rstrip("/"),
            token,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": api_version},
            cache=cache,
        )


class GitHubApi:
    """Typed issue operations over an injectable JSON client."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def current_user(self) -> UserWire:
        """Return the authenticated GitHub account."""
        return parse_json(self._client.get(routes.CURRENT_USER), UserWire)

    def repositories(self, page: int) -> list[RepositoryWire]:
        """Return one page of repositories visible to the account."""
        response = parse_json(
            self._client.get(routes.REPOSITORIES, {"per_page": 100, "page": page, "sort": "updated"}),
            RepositoriesWire,
        )
        return response.root

    def labels(self, repository: str, page: int) -> list[str]:
        """Return one page of label names."""
        response = parse_json(
            self._client.get(routes.labels(repository), {"per_page": 100, "page": page}),
            LabelsWire,
        )
        return [label.name for label in response.root]

    def issues(self, repository: str, page: int) -> list[IssueWire]:
        """Return one page of issues and pull requests."""
        response = parse_json(
            self._client.get(
                routes.issues(repository),
                {"per_page": 100, "page": page, "state": "all", "sort": "created", "direction": "asc"},
            ),
            IssuesWire,
        )
        return response.root

    def issue(self, repository: str, number: object) -> IssueWire:
        """Return one issue by repository-local number."""
        return parse_json(self._client.get(routes.issue(repository, number)), IssueWire)

    def comments(
        self,
        repository: str,
        number: object,
        page: int,
    ) -> list[CommentWire]:
        """Return one page of issue-conversation comments."""
        return parse_json(
            self._client.get(
                routes.issue_comments(repository, number),
                {"per_page": 100, "page": page},
            ),
            CommentsWire,
        ).root

    def create_comment(self, repository: str, number: object, payload: JsonObject) -> CommentWire:
        """Create one issue comment; POST is deliberately never retried."""
        return parse_json(
            self._client.post(routes.issue_comments(repository, number), payload),
            CommentWire,
        )

    def issue_types(self, repository: str, *, ttl: float) -> IssueTypesWire:
        """Return organization issue types enabled for a repository."""
        return parse_json(
            self._client.get(routes.issue_types(repository), ttl=ttl, label="issue types"),
            IssueTypesWire,
        )

    def create_issue(self, repository: str, payload: JsonObject) -> IssueWire:
        """Create an issue and validate GitHub's response."""
        return parse_json(self._client.post(routes.issues(repository), payload), IssueWire)

    def update_issue(self, repository: str, number: object, payload: JsonObject) -> None:
        """Apply one atomic issue PATCH."""
        self._client.patch(routes.issue(repository, number), payload)

    def replace_labels(self, repository: str, number: object, labels: list[str]) -> None:
        """Replace the complete label set on one issue."""
        self._client.put(routes.issue_labels(repository, number), {"labels": labels})


__all__ = ["GitHubApi", "GitHubClient"]
