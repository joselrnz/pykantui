"""Pydantic models for Plane wire responses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


class PlaneWireModel(BaseModel):
    """Base response model tolerant of additive Plane fields."""

    model_config = ConfigDict(extra="ignore")


class ProjectWire(PlaneWireModel):
    """Plane project record."""

    id: str = ""
    identifier: str = ""
    name: str = ""
    description: str = ""


class StateWire(PlaneWireModel):
    """Plane workflow-state record."""

    id: str = ""
    name: str = ""
    group: str = ""
    sequence: int | float | str | None = None


class LabelWire(PlaneWireModel):
    """Plane label record."""

    id: str = ""
    name: str = ""


class MemberIdentityWire(PlaneWireModel):
    """Nested or direct Plane member identity."""

    id: str = ""
    email: str = ""
    display_name: str = ""


class MemberWire(MemberIdentityWire):
    """Plane member response, sometimes wrapping its identity."""

    member: MemberIdentityWire | None = None


class MembersWire(RootModel[list[MemberWire]]):
    """Bare member-list response."""


class MembersPageWire(PlaneWireModel):
    """Paged member response used by some Plane versions."""

    results: list[MemberWire] = Field(default_factory=list)


class WorkItemWire(PlaneWireModel):
    """Plane work-item fields used by pykantui."""

    id: str = ""
    type_id: str | None = None
    sequence_id: int | str | None = None
    name: str = ""
    state: str = ""
    state_group: str = ""
    description_html: str = ""
    description_stripped: str = ""
    priority: str = ""
    assignees: list[str] = Field(default_factory=list)
    created_by: str = ""
    labels: list[str | LabelWire] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    start_date: str | None = None
    completed_at: str | None = None
    target_date: str | None = None
    parent: str | None = None
    sort_order: int | float | str | None = None
    estimate_point: int | float | str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_sparse_or_expanded_response(cls, value: Any) -> Any:
        """Normalize the create/retrieve shapes into the list-response shape.

        Plane's official API models declare most work-item values nullable and
        allow ``state``/people to be either ids or expanded objects. The create
        endpoint commonly returns that sparse/expanded form, while list calls
        return ids. Keeping one normalized wire model prevents a successful
        POST from being mistaken for a rejected create during response parsing.
        """
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)

        state = normalized.get("state")
        if isinstance(state, Mapping):
            normalized["state"] = str(state.get("id") or "")
            normalized["state_group"] = str(
                normalized.get("state_group") or state.get("group") or ""
            )
        elif state is None:
            normalized["state"] = ""

        creator = normalized.get("created_by")
        if isinstance(creator, Mapping):
            normalized["created_by"] = str(creator.get("id") or "")

        assignees = normalized.get("assignees")
        if assignees is None:
            normalized["assignees"] = []
        elif isinstance(assignees, (list, tuple)):
            normalized["assignees"] = [
                str(item.get("id") or "") if isinstance(item, Mapping) else item
                for item in assignees
            ]

        if normalized.get("labels") is None:
            normalized["labels"] = []

        for field_name in (
            "id",
            "name",
            "state_group",
            "description_html",
            "description_stripped",
            "priority",
            "created_by",
        ):
            if normalized.get(field_name) is None:
                normalized[field_name] = ""
        return normalized


class CommentActorWire(PlaneWireModel):
    """Expanded Plane comment actor."""

    id: str = ""
    display_name: str = ""
    email: str = ""


class CommentWire(PlaneWireModel):
    """One current Plane work-item comment."""

    id: str
    comment_html: str = ""
    comment_stripped: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    edited_at: str | None = None
    deleted_at: str | None = None
    created_by: str = ""
    actor: CommentActorWire | str | None = None
    parent: str | None = None
    access: str = ""

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """Reject records that cannot be tracked across later syncs."""
        if not value.strip():
            raise ValueError("comment id must not be blank")
        return value


class CommentsPageWire(PlaneWireModel):
    """Cursor page whose completeness metadata must be explicit."""

    results: list[CommentWire]
    next_page_results: bool
    next_cursor: str | None = None


__all__ = [
    "LabelWire",
    "CommentWire",
    "CommentsPageWire",
    "MemberWire",
    "MembersPageWire",
    "MembersWire",
    "ProjectWire",
    "StateWire",
    "WorkItemWire",
]
