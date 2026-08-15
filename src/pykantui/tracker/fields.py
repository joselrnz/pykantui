"""Typed card-field contracts owned by each provider.

The normalised names let the TUI build one consistent editor.  The native key,
availability and create/edit flags remain provider data, so a Jira assignee is
never accidentally encoded like a Trello member or a Monday People column.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class CardFieldName(StrEnum):
    TITLE = "title"
    BODY = "body"
    COLUMN = "column_id"
    ASSIGNEE = "assignee"
    ISSUE_TYPE = "issue_type"
    PRIORITY = "priority"
    LABELS = "labels"
    COMPONENTS = "components"
    DUE_DATE = "due_date"


CARD_FIELD_ORDER: tuple[CardFieldName, ...] = tuple(CardFieldName)


class CardFieldKind(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    COLUMN = "column"
    USER = "user"
    ISSUE_TYPE = "issue_type"
    PRIORITY = "priority"
    LABELS = "labels"
    COMPONENTS = "components"
    DATE = "date"


FIELD_KINDS: Mapping[CardFieldName, CardFieldKind] = {
    CardFieldName.TITLE: CardFieldKind.TEXT,
    CardFieldName.BODY: CardFieldKind.MARKDOWN,
    CardFieldName.COLUMN: CardFieldKind.COLUMN,
    CardFieldName.ASSIGNEE: CardFieldKind.USER,
    CardFieldName.ISSUE_TYPE: CardFieldKind.ISSUE_TYPE,
    CardFieldName.PRIORITY: CardFieldKind.PRIORITY,
    CardFieldName.LABELS: CardFieldKind.LABELS,
    CardFieldName.COMPONENTS: CardFieldKind.COMPONENTS,
    CardFieldName.DUE_DATE: CardFieldKind.DATE,
}


class CardFieldSpec(BaseModel):
    """One provider's contract for one normalised card value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CardFieldName
    kind: CardFieldKind
    provider_key: str = ""
    editable: bool = False
    creatable: bool = False
    required_on_create: bool = False
    clearable: bool = True
    configuration_key: str = ""

    @field_validator("provider_key")
    @classmethod
    def _supported_fields_need_a_native_key(cls, value: str, info: object) -> str:
        del info
        return value.strip()

    def available(self, config: Mapping[str, object] | None = None) -> bool:
        if not self.configuration_key or config is None:
            return True
        return bool(config.get(self.configuration_key))


def card_schema(
    native_keys: Mapping[CardFieldName, str],
    *,
    editable: Collection[CardFieldName] = (),
    creatable: Collection[CardFieldName] = (),
    required: Collection[CardFieldName] = (CardFieldName.TITLE,),
    configured_by: Mapping[CardFieldName, str] | None = None,
    not_clearable: Collection[CardFieldName] = (CardFieldName.TITLE,),
) -> tuple[CardFieldSpec, ...]:
    """Build a complete, ordered schema from one provider's explicit mapping."""

    native = dict(native_keys)
    edits = set(editable)
    creates = set(creatable)
    required_fields = set(required)
    configured = dict(configured_by or {})
    uncleared = set(not_clearable)

    supported = edits | creates
    missing = supported - native.keys()
    if missing:
        raise ValueError(f"supported card fields need provider keys: {', '.join(sorted(missing))}")

    return tuple(
        CardFieldSpec(
            name=name,
            kind=FIELD_KINDS[name],
            provider_key=native.get(name, ""),
            editable=name in edits,
            creatable=name in creates,
            required_on_create=name in required_fields and name in creates,
            clearable=name not in uncleared,
            configuration_key=configured.get(name, ""),
        )
        for name in CARD_FIELD_ORDER
    )
