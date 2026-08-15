"""Provider-capability visibility rules for Rows and Split detail fields."""

from __future__ import annotations

from collections.abc import Collection

from pykantui.core.work_items import WorkItemColumn
from pykantui.tui.widgets.card_fields import Field

_CORE_DETAIL_FIELDS = frozenset({"summary", "status", "key"})
_PROVIDER_FIELD_COLUMNS = {
    "assignee": WorkItemColumn.ASSIGNEE,
    "issue_type": WorkItemColumn.TYPE,
    "reporter": WorkItemColumn.REPORTER,
    "priority": WorkItemColumn.PRIORITY,
    "due": WorkItemColumn.DUE,
    "labels": WorkItemColumn.LABELS,
    "components": WorkItemColumn.COMPONENTS,
    "created": WorkItemColumn.CREATED,
}
_EMPTY_AUDIT_VALUES = frozenset({"", "—", "N/A"})


def detail_field_visible(
    field: Field,
    *,
    value: str,
    available: Collection[WorkItemColumn],
) -> bool:
    """Return whether a field belongs to the active provider's detail pane.

    Provider-backed fields follow the declared capability contract even when
    one card is unassigned. Provider-specific audit metadata has no equivalent
    capability flag, so it appears only when the selected card contains it.
    """
    if field.key in _CORE_DETAIL_FIELDS:
        return True
    column = _PROVIDER_FIELD_COLUMNS.get(field.key)
    if column is not None:
        return column in available
    return value.strip() not in _EMPTY_AUDIT_VALUES


def editable_field_available(
    field: Field,
    available: Collection[WorkItemColumn],
) -> bool:
    """Gate a structured editor field with the same provider contract."""
    if field.key in _CORE_DETAIL_FIELDS:
        return True
    column = _PROVIDER_FIELD_COLUMNS.get(field.key)
    return column is None or column in available

