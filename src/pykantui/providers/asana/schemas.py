"""Pydantic models for the Asana response fragments pykantui consumes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AsanaWireModel(BaseModel):
    """Base model that permits additive fields in Asana responses."""

    model_config = ConfigDict(extra="ignore")


class ReferenceWire(AsanaWireModel):
    """A named Asana resource reference."""

    gid: str = ""
    name: str = ""
    email: str = ""


class UserWire(ReferenceWire):
    """Authenticated account or workspace member."""


class WorkspaceWire(ReferenceWire):
    """Workspace discovery record."""


class ProjectWire(ReferenceWire):
    """Project discovery record."""

    notes: str = ""
    permalink_url: str = ""
    archived: bool = False
    workspace: ReferenceWire | None = None


class SectionWire(ReferenceWire):
    """Board section record."""


class TagWire(ReferenceWire):
    """Task tag record."""


class MembershipWire(AsanaWireModel):
    """The task placement within one project."""

    project: ReferenceWire = Field(default_factory=ReferenceWire)
    section: SectionWire | None = None


class TaskWire(ReferenceWire):
    """Task fields used by Markdown export and conflict detection."""

    notes: str = ""
    completed: bool = False
    completed_at: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    due_on: str | None = None
    permalink_url: str = ""
    assignee: UserWire | None = None
    created_by: UserWire | None = None
    tags: list[TagWire] = Field(default_factory=list)
    parent: ReferenceWire | None = None
    memberships: list[MembershipWire] = Field(default_factory=list)


class StoryWire(AsanaWireModel):
    """One task story; only ``comment_added`` records are user comments."""

    gid: str
    resource_subtype: str = ""
    type: str = ""
    text: str | None = None
    html_text: str | None = None
    created_at: str | None = None
    created_by: UserWire | None = None


class CursorWire(AsanaWireModel):
    """Opaque Asana continuation token."""

    offset: str = ""


class UserEnvelope(AsanaWireModel):
    """Single-user response envelope."""

    data: UserWire = Field(default_factory=UserWire)


class ProjectEnvelope(AsanaWireModel):
    """Single-project response envelope."""

    data: ProjectWire = Field(default_factory=ProjectWire)


class TaskEnvelope(AsanaWireModel):
    """Single-task response envelope."""

    data: TaskWire = Field(default_factory=TaskWire)


class StoryEnvelope(AsanaWireModel):
    """Single-story response envelope."""

    data: StoryWire


class WorkspacePage(AsanaWireModel):
    """Workspace list response."""

    data: list[WorkspaceWire] = Field(default_factory=list)


class ProjectPage(AsanaWireModel):
    """Project page response."""

    data: list[ProjectWire] = Field(default_factory=list)
    next_page: CursorWire | None = None


class SectionPage(AsanaWireModel):
    """Section page response."""

    data: list[SectionWire] = Field(default_factory=list)
    next_page: CursorWire | None = None


class TaskPage(AsanaWireModel):
    """Task page response."""

    data: list[TaskWire] = Field(default_factory=list)
    next_page: CursorWire | None = None


class StoryPage(AsanaWireModel):
    """Task-story page with Asana's opaque continuation token."""

    data: list[StoryWire]
    next_page: CursorWire | None = None


class UserPage(AsanaWireModel):
    """Workspace-user response."""

    data: list[UserWire] = Field(default_factory=list)


__all__ = [
    "ProjectEnvelope",
    "ProjectPage",
    "ProjectWire",
    "SectionPage",
    "SectionWire",
    "StoryEnvelope",
    "StoryPage",
    "StoryWire",
    "TaskEnvelope",
    "TaskPage",
    "TaskWire",
    "UserEnvelope",
    "UserPage",
    "UserWire",
    "WorkspacePage",
]
