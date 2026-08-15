"""Pydantic models for Shortcut wire responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator


class ShortcutWireModel(BaseModel):
    """Base response model tolerant of additive Shortcut fields."""

    model_config = ConfigDict(extra="ignore")


class ProfileWire(ShortcutWireModel):
    """Member profile fragment."""

    name: str = ""
    mention_name: str = ""


class MemberWire(ShortcutWireModel):
    """Shortcut member record."""

    id: str | int = ""
    mention_name: str = ""
    name: str = ""
    profile: ProfileWire | None = None


class StateWire(ShortcutWireModel):
    """Workflow state record."""

    id: str | int = ""
    name: str = ""
    type: str = ""
    position: int | float | str | None = None


class WorkflowWire(ShortcutWireModel):
    """Workflow board record."""

    id: str | int = ""
    name: str = ""
    description: str = ""
    team_id: str | int | None = None
    states: list[StateWire] = Field(default_factory=list)


class LabelWire(ShortcutWireModel):
    """Story label record."""

    name: str = ""


class StoryWire(ShortcutWireModel):
    """Story fields used by pykantui."""

    id: str | int = ""
    name: str = ""
    description: str = ""
    workflow_state_id: str | int = ""
    story_type: str = ""
    owner_ids: list[str | int] = Field(default_factory=list)
    requested_by_id: str | int = ""
    labels: list[LabelWire] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    deadline: str | None = None
    epic_id: str | int | None = None
    position: int | float | str | None = None
    app_url: str = ""


class WorkflowsWire(RootModel[list[WorkflowWire]]):
    """Workflow-list response."""


class MembersWire(RootModel[list[MemberWire]]):
    """Member-list response."""


class SearchPageWire(ShortcutWireModel):
    """Story-search page with its provider-supplied continuation path."""

    data: list[StoryWire] = Field(default_factory=list)
    next: str | None = None


class CommentWire(ShortcutWireModel):
    """One Shortcut story comment."""

    id: str | int
    story_id: str | int = ""
    author_id: str | int | None = None
    text: str | None = ""
    created_at: str | None = None
    updated_at: str | None = None
    deleted: bool = False
    app_url: str = ""
    parent_id: str | int | None = None
    position: int | float | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str | int) -> str | int:
        """Reject records that cannot be tracked across later syncs."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("comment id must not be blank")
        return value


class CommentsWire(RootModel[list[CommentWire]]):
    """Shortcut's complete unpaged story-comment response."""


__all__ = [
    "MemberWire",
    "CommentWire",
    "CommentsWire",
    "MembersWire",
    "SearchPageWire",
    "StateWire",
    "StoryWire",
    "WorkflowWire",
    "WorkflowsWire",
]
