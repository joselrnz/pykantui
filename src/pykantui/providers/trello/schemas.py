"""Pydantic models for Trello wire responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator


class TrelloWireModel(BaseModel):
    """Base response model tolerant of additive Trello fields."""

    model_config = ConfigDict(extra="ignore")


class MemberWire(TrelloWireModel):
    """Trello member record."""

    id: str = ""
    fullName: str = ""  # noqa: N815 - provider wire key
    username: str = ""
    # The field is present but null on accounts that do not expose an email.
    # Observed against the live API on 2026-08-12.
    email: str | None = None


class ActionDataWire(TrelloWireModel):
    """Data payload carried by a Trello action."""

    text: str = ""


class ActionWire(TrelloWireModel):
    """Creation action embedded with a card to identify its reporter."""

    type: str = ""
    id: str = ""
    idMemberCreator: str = ""  # noqa: N815 - provider wire key
    memberCreator: MemberWire | None = None  # noqa: N815 - provider wire key
    data: ActionDataWire = Field(default_factory=ActionDataWire)
    date: str | None = None


class CommentActionWire(ActionWire):
    """Comment action with the stable identity required by comment sync."""

    id: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """Reject comments that cannot be tracked across later syncs."""
        if not value.strip():
            raise ValueError("comment action id must not be blank")
        return value


class BoardWire(TrelloWireModel):
    """Trello board record."""

    id: str = ""
    name: str = ""
    desc: str = ""
    url: str = ""
    closed: bool = False


class ListWire(TrelloWireModel):
    """Trello board list record."""

    id: str = ""
    name: str = ""
    pos: int | float | str | None = None


class LabelWire(TrelloWireModel):
    """Trello card or board label."""

    id: str = ""
    name: str = ""
    color: str = ""


class CardWire(TrelloWireModel):
    """Trello card fields used by pykantui."""

    id: str = ""
    idShort: int | None = None  # noqa: N815 - provider wire key
    name: str = ""
    desc: str = ""
    idList: str = ""  # noqa: N815 - provider wire key
    due: str | None = None
    dueComplete: bool = False  # noqa: N815 - provider wire key
    pos: int | float | str | None = None
    url: str = ""
    labels: list[LabelWire] = Field(default_factory=list)
    dateLastActivity: str | None = None  # noqa: N815 - provider wire key
    closed: bool = False
    idMembers: list[str] = Field(default_factory=list)  # noqa: N815 - provider wire key
    actions: list[ActionWire | None] = Field(default_factory=list)


class BoardsWire(RootModel[list[BoardWire]]):
    """Board-list response."""


class ListsWire(RootModel[list[ListWire]]):
    """List-list response."""


class CardsWire(RootModel[list[CardWire]]):
    """Card-list response."""


class MembersWire(RootModel[list[MemberWire]]):
    """Member-list response."""


class LabelsWire(RootModel[list[LabelWire]]):
    """Label-list response."""


class ActionsWire(RootModel[list[CommentActionWire]]):
    """Card-action response used for commentCard actions."""


__all__ = [
    "ActionWire",
    "ActionsWire",
    "BoardWire",
    "BoardsWire",
    "CardWire",
    "CommentActionWire",
    "CardsWire",
    "LabelWire",
    "LabelsWire",
    "ListWire",
    "ListsWire",
    "MemberWire",
    "MembersWire",
]
