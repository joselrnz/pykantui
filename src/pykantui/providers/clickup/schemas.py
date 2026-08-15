"""Pydantic models for ClickUp wire responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClickUpWireModel(BaseModel):
    """Base response model tolerant of additive ClickUp fields."""

    model_config = ConfigDict(extra="ignore")


class UserWire(ClickUpWireModel):
    """ClickUp user fragment."""

    id: str | int = ""
    username: str = ""
    email: str = ""


class UserEnvelope(ClickUpWireModel):
    """Current-user response."""

    user: UserWire = Field(default_factory=UserWire)


class TeamWire(ClickUpWireModel):
    """Workspace/team record."""

    id: str | int = ""
    name: str = ""


class TeamsEnvelope(ClickUpWireModel):
    """Workspace/team collection response."""

    teams: list[TeamWire] = Field(default_factory=list)


class SpaceWire(ClickUpWireModel):
    """Space record."""

    id: str | int = ""
    name: str = ""


class SpacesEnvelope(ClickUpWireModel):
    """Space collection response."""

    spaces: list[SpaceWire] = Field(default_factory=list)


class StatusWire(ClickUpWireModel):
    """List/task status record."""

    status: str = ""
    type: str = ""
    orderindex: int | float | str | None = None


class ListWire(ClickUpWireModel):
    """ClickUp list record."""

    id: str | int = ""
    name: str = ""
    content: str = ""
    statuses: list[StatusWire] = Field(default_factory=list)


class ListsEnvelope(ClickUpWireModel):
    """List collection response."""

    lists: list[ListWire] = Field(default_factory=list)


class FolderWire(ClickUpWireModel):
    """Folder with its embedded lists."""

    id: str | int = ""
    name: str = ""
    lists: list[ListWire] = Field(default_factory=list)


class FoldersEnvelope(ClickUpWireModel):
    """Folder collection response."""

    folders: list[FolderWire] = Field(default_factory=list)


class PriorityWire(ClickUpWireModel):
    """Task priority fragment."""

    priority: str = ""


class CustomItemTypeWire(ClickUpWireModel):
    """One workspace custom task type."""

    id: str | int = ""
    name: str = ""


class CustomItemTypesEnvelope(ClickUpWireModel):
    """Workspace custom task type collection."""

    custom_items: list[CustomItemTypeWire] = Field(default_factory=list)


class TagWire(ClickUpWireModel):
    """Task tag fragment."""

    name: str = ""


class TaskWire(ClickUpWireModel):
    """ClickUp task fields used by pykantui."""

    id: str = ""
    custom_id: str | None = None
    custom_item_id: str | int | None = None
    team_id: str | int = ""
    name: str = ""
    status: StatusWire = Field(default_factory=StatusWire)
    text_content: str | None = None
    description: str | None = None
    priority: PriorityWire | None = None
    assignees: list[UserWire] = Field(default_factory=list)
    creator: UserWire | None = None
    tags: list[TagWire] = Field(default_factory=list)
    date_created: str | int | None = None
    date_updated: str | int | None = None
    start_date: str | int | None = None
    date_closed: str | int | None = None
    due_date: str | int | None = None
    parent: str | None = None
    orderindex: int | float | str | None = None
    url: str = ""


class CommentPartWire(ClickUpWireModel):
    """One fragment from ClickUp's rich comment representation."""

    text: str = ""


class CommentWire(ClickUpWireModel):
    """One task comment returned by ClickUp."""

    id: str | int
    comment_text: str | None = None
    comment: list[CommentPartWire] = Field(default_factory=list)
    date: str | int
    user: UserWire | None = None
    reply_count: int = 0


class CommentsEnvelope(ClickUpWireModel):
    """One reverse-chronological task-comment page."""

    comments: list[CommentWire]


class CreatedCommentWire(ClickUpWireModel):
    """ClickUp's intentionally partial create-comment response."""

    id: str | int
    hist_id: str | int = ""
    date: str | int | None = None


class TasksEnvelope(ClickUpWireModel):
    """Task page response."""

    tasks: list[TaskWire] = Field(default_factory=list)


class MembersEnvelope(ClickUpWireModel):
    """Assignable member response."""

    members: list[UserWire] = Field(default_factory=list)


__all__ = [
    "CommentWire",
    "CommentsEnvelope",
    "CreatedCommentWire",
    "CustomItemTypesEnvelope",
    "CustomItemTypeWire",
    "FoldersEnvelope",
    "ListWire",
    "ListsEnvelope",
    "MembersEnvelope",
    "SpacesEnvelope",
    "SpaceWire",
    "StatusWire",
    "TaskWire",
    "TasksEnvelope",
    "TeamsEnvelope",
    "TeamWire",
    "UserEnvelope",
    "UserWire",
]
