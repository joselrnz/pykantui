"""Pydantic models for Forgejo response fragments."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel


class ForgejoWireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UserWire(ForgejoWireModel):
    id: int | str = ""
    login: str = ""
    full_name: str | None = None
    email: str | None = None


class RepositoryWire(ForgejoWireModel):
    id: int | str = ""
    full_name: str = ""
    name: str = ""
    owner: UserWire = Field(default_factory=UserWire)
    description: str | None = None
    html_url: str | None = None
    private: bool = False
    archived: bool = False
    has_issues: bool = True


class LabelWire(ForgejoWireModel):
    id: int | str = ""
    name: str = ""


class IssueWire(ForgejoWireModel):
    id: int | str = ""
    number: int | None = None
    title: str | None = None
    body: str | None = None
    state: str = ""
    labels: list[LabelWire] = Field(default_factory=list)
    assignees: list[UserWire] = Field(default_factory=list)
    user: UserWire | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    due_date: str | None = None
    html_url: str | None = None
    pull_request: dict[str, object] | None = None


class CommentWire(ForgejoWireModel):
    id: int | str
    body: str | None = None
    user: UserWire | None = None
    created_at: str | None = None
    updated_at: str | None = None
    html_url: str | None = None


class RepositoriesWire(RootModel[list[RepositoryWire]]):
    pass


class LabelsWire(RootModel[list[LabelWire]]):
    pass


class IssuesWire(RootModel[list[IssueWire]]):
    pass


class CommentsWire(RootModel[list[CommentWire]]):
    pass


__all__ = [
    "CommentWire",
    "CommentsWire",
    "IssueWire",
    "IssuesWire",
    "LabelWire",
    "LabelsWire",
    "RepositoriesWire",
    "RepositoryWire",
    "UserWire",
]
