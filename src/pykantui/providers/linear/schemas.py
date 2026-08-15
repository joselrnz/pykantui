"""Pydantic models for Linear GraphQL response data."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LinearWireModel(BaseModel):
    """Base response model tolerant of additive Linear fields."""

    model_config = ConfigDict(extra="ignore")


class PageInfoWire(LinearWireModel):
    """Linear Relay pagination metadata."""

    hasNextPage: bool = False  # noqa: N815 - provider wire key
    endCursor: str | None = None  # noqa: N815 - provider wire key


class UserWire(LinearWireModel):
    """Linear user fragment."""

    id: str = ""
    name: str = ""
    displayName: str = ""  # noqa: N815 - provider wire key
    email: str = ""


class ViewerDataWire(LinearWireModel):
    """Viewer query data."""

    viewer: UserWire = Field(default_factory=UserWire)


class TeamWire(LinearWireModel):
    """Linear team record."""

    id: str = ""
    key: str = ""
    name: str = ""
    description: str | None = None


class TeamConnectionWire(LinearWireModel):
    """Paged team connection."""

    pageInfo: PageInfoWire = Field(default_factory=PageInfoWire)  # noqa: N815
    nodes: list[TeamWire] = Field(default_factory=list)


class TeamsDataWire(LinearWireModel):
    """Teams query data."""

    teams: TeamConnectionWire = Field(default_factory=TeamConnectionWire)


class StateWire(LinearWireModel):
    """Linear workflow state."""

    id: str = ""
    name: str = ""
    type: str = ""
    position: int | float | str | None = None


class StateConnectionWire(LinearWireModel):
    """Workflow-state connection."""

    nodes: list[StateWire] = Field(default_factory=list)


class LabelWire(LinearWireModel):
    """Linear issue label."""

    id: str = ""
    name: str = ""


class LabelConnectionWire(LinearWireModel):
    """Label connection."""

    nodes: list[LabelWire] = Field(default_factory=list)


class ParentWire(LinearWireModel):
    """Parent-issue reference."""

    identifier: str = ""


class IssueWire(LinearWireModel):
    """Linear issue fields used by pykantui."""

    id: str = ""
    identifier: str = ""
    title: str = ""
    description: str = ""
    url: str = ""
    priorityLabel: str = ""  # noqa: N815 - provider wire key
    sortOrder: int | float | str | None = None  # noqa: N815
    createdAt: str | None = None  # noqa: N815
    updatedAt: str | None = None  # noqa: N815
    startedAt: str | None = None  # noqa: N815
    completedAt: str | None = None  # noqa: N815
    dueDate: str | None = None  # noqa: N815
    state: StateWire = Field(default_factory=StateWire)
    assignee: UserWire | None = None
    creator: UserWire | None = None
    parent: ParentWire | None = None
    labels: LabelConnectionWire = Field(default_factory=LabelConnectionWire)


class IssueConnectionWire(LinearWireModel):
    """Paged issue connection."""

    pageInfo: PageInfoWire = Field(default_factory=PageInfoWire)  # noqa: N815
    nodes: list[IssueWire] = Field(default_factory=list)


class TeamDetailWire(LinearWireModel):
    """Team fields returned by state and issue queries."""

    states: StateConnectionWire = Field(default_factory=StateConnectionWire)
    issues: IssueConnectionWire = Field(default_factory=IssueConnectionWire)


class TeamDataWire(LinearWireModel):
    """Single-team query data."""

    team: TeamDetailWire = Field(default_factory=TeamDetailWire)


class IssueDataWire(LinearWireModel):
    """Single-issue query data."""

    issue: IssueWire | None = None


class IssueCreateWire(LinearWireModel):
    """Issue-create mutation result."""

    success: bool = False
    issue: IssueWire | None = None


class IssueCreateDataWire(LinearWireModel):
    """Issue-create mutation data."""

    issueCreate: IssueCreateWire = Field(default_factory=IssueCreateWire)  # noqa: N815


class UserConnectionWire(LinearWireModel):
    """User connection."""

    nodes: list[UserWire] = Field(default_factory=list)


class UsersDataWire(LinearWireModel):
    """Users query data."""

    users: UserConnectionWire = Field(default_factory=UserConnectionWire)


class LabelsDataWire(LinearWireModel):
    """Workspace issue-label query data."""

    issueLabels: LabelConnectionWire = Field(default_factory=LabelConnectionWire)  # noqa: N815


class CommentWire(LinearWireModel):
    """One Linear issue comment, including non-user actors."""

    id: str
    issueId: str = ""  # noqa: N815
    body: str = ""
    url: str = ""
    createdAt: str | None = None  # noqa: N815
    updatedAt: str | None = None  # noqa: N815
    parentId: str | None = None  # noqa: N815
    user: UserWire | None = None
    botActor: UserWire | None = None  # noqa: N815
    externalUser: UserWire | None = None  # noqa: N815

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """Reject records that cannot be tracked across later syncs."""
        if not value.strip():
            raise ValueError("comment id must not be blank")
        return value


class CommentPageInfoWire(LinearWireModel):
    """Required Relay metadata for a complete comment connection."""

    hasNextPage: bool  # noqa: N815
    endCursor: str | None  # noqa: N815


class CommentConnectionWire(LinearWireModel):
    """Relay-paged comment connection."""

    pageInfo: CommentPageInfoWire  # noqa: N815
    nodes: list[CommentWire]


class IssueCommentsWire(LinearWireModel):
    """Issue fragment containing its comment connection."""

    comments: CommentConnectionWire


class CommentsDataWire(LinearWireModel):
    """Comment-list GraphQL data."""

    issue: IssueCommentsWire


class CommentCreateWire(LinearWireModel):
    """Comment-create mutation result."""

    success: bool = False
    comment: CommentWire | None = None


class CommentCreateDataWire(LinearWireModel):
    """Comment-create GraphQL data."""

    commentCreate: CommentCreateWire = Field(default_factory=CommentCreateWire)  # noqa: N815


__all__ = [
    "IssueCreateDataWire",
    "CommentCreateDataWire",
    "CommentWire",
    "CommentsDataWire",
    "IssueDataWire",
    "IssueWire",
    "LabelsDataWire",
    "StateWire",
    "TeamDataWire",
    "TeamsDataWire",
    "TeamWire",
    "UsersDataWire",
    "UserWire",
    "ViewerDataWire",
]
