"""Pydantic models for GitHub's wire responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel


class GitHubWireModel(BaseModel):
    """Base response model that tolerates additive GitHub fields."""

    model_config = ConfigDict(extra="ignore")


class UserWire(GitHubWireModel):
    """GitHub account fragment used by users, assignees, and reporters."""

    id: int | str = ""
    login: str = ""
    name: str | None = None
    email: str | None = None


class RepositoryWire(GitHubWireModel):
    """Repository fields needed by project discovery."""

    full_name: str = ""
    name: str = ""
    owner: UserWire = Field(default_factory=UserWire)
    description: str | None = None
    html_url: str | None = None
    private: bool = False


class LabelWire(GitHubWireModel):
    """GitHub label fragment."""

    name: str = ""


class IssueTypeWire(GitHubWireModel):
    """GitHub organization issue type."""

    id: int | str = ""
    name: str = ""


class IssueWire(GitHubWireModel):
    """Issue fields used by Markdown export and conflict detection."""

    id: int | str = ""
    number: int | None = None
    title: str | None = None
    body: str | None = None
    state: str = ""
    type: IssueTypeWire | str | None = None
    labels: list[LabelWire] = Field(default_factory=list)
    assignees: list[UserWire] = Field(default_factory=list)
    user: UserWire | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    html_url: str | None = None
    pull_request: dict[str, object] | None = None


class CommentWire(GitHubWireModel):
    """One GitHub issue-conversation comment (not a PR review comment)."""

    id: int | str
    body: str | None = None
    user: UserWire | None = None
    created_at: str
    updated_at: str | None = None
    html_url: str | None = None


class RepositoriesWire(RootModel[list[RepositoryWire]]):
    """Repository-list response."""


class LabelsWire(RootModel[list[LabelWire]]):
    """Label-list response."""


class IssuesWire(RootModel[list[IssueWire]]):
    """Issue-list response."""


class CommentsWire(RootModel[list[CommentWire]]):
    """Issue-comment list response."""


class IssueTypesWire(RootModel[list[IssueTypeWire]]):
    """Issue-type-list response."""


__all__ = [
    "CommentWire",
    "CommentsWire",
    "IssueTypeWire",
    "IssueTypesWire",
    "IssueWire",
    "IssuesWire",
    "LabelWire",
    "LabelsWire",
    "RepositoriesWire",
    "RepositoryWire",
    "UserWire",
]
