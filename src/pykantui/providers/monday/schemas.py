"""Pydantic models for Monday.com GraphQL response data."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MondayWireModel(BaseModel):
    """Base response model tolerant of additive Monday fields."""

    model_config = ConfigDict(extra="ignore")


class UserWire(MondayWireModel):
    """Monday user fragment."""

    id: int | str = ""
    name: str = ""
    email: str = ""


class MeDataWire(MondayWireModel):
    """Authenticated-user query data."""

    me: UserWire = Field(default_factory=UserWire)


class BoardSummaryWire(MondayWireModel):
    """Monday board-list record."""

    id: int | str = ""
    name: str = ""
    description: str = ""
    url: str = ""


class BoardsDataWire(MondayWireModel):
    """Board-list query data."""

    boards: list[BoardSummaryWire] = Field(default_factory=list)


class ColumnWire(MondayWireModel):
    """Monday board column metadata."""

    id: str = ""
    title: str = ""
    type: str = ""
    settings_str: str | None = None


class GroupWire(MondayWireModel):
    """Monday board group metadata."""

    id: str = ""
    title: str = ""
    position: int | float | str | None = None


class ColumnValueWire(MondayWireModel):
    """One item column value."""

    id: str = ""
    type: str = ""
    text: str = ""
    value: str | None = None


class ItemWire(MondayWireModel):
    """Monday item fields used by pykantui."""

    id: int | str = ""
    name: str = ""
    url: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    creator: UserWire | None = None
    group: GroupWire = Field(default_factory=GroupWire)
    column_values: list[ColumnValueWire] = Field(default_factory=list)


class ItemsPageWire(MondayWireModel):
    """Cursor-paged item connection."""

    cursor: str | None = None
    items: list[ItemWire] = Field(default_factory=list)


class BoardShapeWire(MondayWireModel):
    """Board columns, groups, and optional item page."""

    columns: list[ColumnWire] = Field(default_factory=list)
    groups: list[GroupWire] = Field(default_factory=list)
    items_page: ItemsPageWire = Field(default_factory=ItemsPageWire)


class BoardShapesDataWire(MondayWireModel):
    """Board-shape or board-items query data."""

    boards: list[BoardShapeWire] = Field(default_factory=list)


class ItemsDataWire(MondayWireModel):
    """Single-item query data."""

    items: list[ItemWire] = Field(default_factory=list)


class EntityIdWire(MondayWireModel):
    """Mutation result containing an entity id."""

    id: int | str = ""


class CreateItemDataWire(MondayWireModel):
    """Create-item mutation data."""

    create_item: EntityIdWire = Field(default_factory=EntityIdWire)


class UsersDataWire(MondayWireModel):
    """Workspace-users query data."""

    users: list[UserWire] = Field(default_factory=list)


class UpdateWire(MondayWireModel):
    """One item update, Monday.com's comment resource."""

    id: int | str
    body: str = ""
    text_body: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    edited_at: str | None = None
    creator_id: int | str = ""
    creator: UserWire | None = None
    replies: list[UpdateWire] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: int | str) -> int | str:
        """Reject records that cannot be tracked across later syncs."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("update id must not be blank")
        return value


class ItemUpdatesWire(MondayWireModel):
    """One item and one page of updates."""

    id: int | str
    updates: list[UpdateWire]


class ItemUpdatesDataWire(MondayWireModel):
    """Item-scoped updates query data."""

    items: list[ItemUpdatesWire]


class CreateUpdateDataWire(MondayWireModel):
    """Create-update mutation data."""

    create_update: UpdateWire


__all__ = [
    "BoardShapeWire",
    "BoardShapesDataWire",
    "BoardSummaryWire",
    "BoardsDataWire",
    "ColumnValueWire",
    "ColumnWire",
    "CreateItemDataWire",
    "CreateUpdateDataWire",
    "GroupWire",
    "ItemWire",
    "ItemsDataWire",
    "ItemUpdatesDataWire",
    "MeDataWire",
    "UserWire",
    "UsersDataWire",
    "UpdateWire",
]
