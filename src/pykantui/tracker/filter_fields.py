"""Provider-owned contracts for the expanded TUI filter bar.

Card values are normalised for local filtering, but their availability and
human names belong to the provider.  This module derives filter controls from
the same card schema used by create/edit so unsupported boxes cannot leak from
one tracker into another.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from pykantui.tracker.fields import CardFieldName, CardFieldSpec


class FilterFieldName(StrEnum):
    """Normalised provider filter controls understood by the TUI."""

    SCOPE = "scope"
    STATUS = "status"
    ASSIGNEE = "assignee"
    ISSUE_TYPE = "issue_type"
    PRIORITY = "priority"
    LABELS = "labels"
    KEY = "key"
    SPRINT = "sprint"
    QUERY = "query"


class ProviderFilterLabels(BaseModel):
    """Provider terminology for otherwise normalised filter values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: str = ""
    status: str = "Status"
    assignee: str = "Assignee"
    issue_type: str = "Type"
    priority: str = "Priority"
    labels: str = "Labels"
    key: str = "Work Item Key"
    key_placeholder: str = "e.g. ITEM-123"
    sprint: str = ""


class FilterFieldSpec(BaseModel):
    """One box in the provider-specific portion of the filter bar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: FilterFieldName
    label: str
    configuration_key: str = ""
    query_language: str = ""
    placeholder: str = ""

    def available(self, config: Mapping[str, object] | None = None) -> bool:
        if not self.configuration_key:
            return True
        return bool(config and config.get(self.configuration_key))


_CARD_FILTERS: tuple[tuple[FilterFieldName, CardFieldName], ...] = (
    (FilterFieldName.ASSIGNEE, CardFieldName.ASSIGNEE),
    (FilterFieldName.ISSUE_TYPE, CardFieldName.ISSUE_TYPE),
    (FilterFieldName.PRIORITY, CardFieldName.PRIORITY),
    (FilterFieldName.LABELS, CardFieldName.LABELS),
)


def filter_schema(
    card_fields: Sequence[CardFieldSpec],
    labels: ProviderFilterLabels,
    *,
    query_language: str = "",
) -> tuple[FilterFieldSpec, ...]:
    """Build filter controls from a provider's card and capability contracts."""

    cards = {field.name: field for field in card_fields}
    fields: list[FilterFieldSpec] = []
    if labels.scope:
        fields.append(FilterFieldSpec(name=FilterFieldName.SCOPE, label=labels.scope))
    fields.append(FilterFieldSpec(name=FilterFieldName.STATUS, label=labels.status))

    for filter_name, card_name in _CARD_FILTERS:
        card = cards.get(card_name)
        if card is None or not card.provider_key:
            continue
        fields.append(
            FilterFieldSpec(
                name=filter_name,
                label=getattr(labels, filter_name.value),
                configuration_key=card.configuration_key,
            )
        )

    fields.append(
        FilterFieldSpec(
            name=FilterFieldName.KEY,
            label=labels.key,
            placeholder=labels.key_placeholder,
        )
    )
    if labels.sprint:
        fields.append(FilterFieldSpec(name=FilterFieldName.SPRINT, label=labels.sprint))
    if query_language:
        fields.append(
            FilterFieldSpec(
                name=FilterFieldName.QUERY,
                label=f"{query_language} Query",
                query_language=query_language,
            )
        )
    return tuple(fields)
