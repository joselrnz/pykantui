"""Typed columns shared by the Rows and Split work-item tables.

The provider says which optional values exist.  The view says which of those
the user wants to see.  Keeping both decisions here prevents a table widget
from guessing capabilities from whichever card happens to be its first row.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from pykantui.core.sorting import SortKey
from pykantui.models import Task

if TYPE_CHECKING:
    from pykantui.tracker.fields import CardFieldSpec


class WorkItemColumn(StrEnum):
    """One provider-neutral column in the Rows and Split tables."""

    SYNC = "sync"
    NUMBER = "number"
    KEY = "key"
    STATUS = "status"
    TYPE = "type"
    SUMMARY = "summary"
    ASSIGNEE = "assignee"
    REPORTER = "reporter"
    PRIORITY = "priority"
    DUE = "due"
    LABELS = "labels"
    COMPONENTS = "components"
    CREATED = "created"


class WorkItemColumnSpec(BaseModel):
    """Rendering and sorting contract for one work-item column."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    column: WorkItemColumn
    label: str
    required: bool = False
    sort_key: SortKey | None = None
    preferred_width: int
    min_width: int = 3

    @property
    def sortable(self) -> bool:
        """Whether selecting the header can establish a sort order."""
        return self.sort_key is not None


CORE_WORK_ITEM_COLUMNS: tuple[WorkItemColumn, ...] = (
    WorkItemColumn.SYNC,
    WorkItemColumn.NUMBER,
    WorkItemColumn.KEY,
    WorkItemColumn.STATUS,
    WorkItemColumn.SUMMARY,
)

DEFAULT_WORK_ITEM_COLUMNS: tuple[WorkItemColumn, ...] = tuple(
    column
    for column in WorkItemColumn
    if column in {*CORE_WORK_ITEM_COLUMNS, WorkItemColumn.TYPE}
)

OPTIONAL_WORK_ITEM_COLUMNS: tuple[WorkItemColumn, ...] = tuple(
    column for column in WorkItemColumn if column not in CORE_WORK_ITEM_COLUMNS
)

LOCAL_WORK_ITEM_COLUMNS: frozenset[WorkItemColumn] = frozenset(
    {
        *CORE_WORK_ITEM_COLUMNS,
        WorkItemColumn.TYPE,
        WorkItemColumn.ASSIGNEE,
        WorkItemColumn.PRIORITY,
        WorkItemColumn.DUE,
        WorkItemColumn.LABELS,
        WorkItemColumn.CREATED,
    }
)

WORK_ITEM_COLUMN_SPECS: Mapping[WorkItemColumn, WorkItemColumnSpec] = {
    WorkItemColumn.SYNC: WorkItemColumnSpec(
        column=WorkItemColumn.SYNC,
        label="⎇",
        required=True,
        preferred_width=14,
        min_width=3,
    ),
    WorkItemColumn.NUMBER: WorkItemColumnSpec(
        column=WorkItemColumn.NUMBER,
        label="#",
        required=True,
        preferred_width=4,
        min_width=2,
    ),
    WorkItemColumn.KEY: WorkItemColumnSpec(
        column=WorkItemColumn.KEY,
        label="Key",
        required=True,
        sort_key=SortKey.KEY,
        preferred_width=14,
        min_width=5,
    ),
    WorkItemColumn.STATUS: WorkItemColumnSpec(
        column=WorkItemColumn.STATUS,
        label="Status",
        required=True,
        sort_key=SortKey.STATUS,
        preferred_width=16,
        min_width=7,
    ),
    WorkItemColumn.TYPE: WorkItemColumnSpec(
        column=WorkItemColumn.TYPE,
        label="Type",
        sort_key=SortKey.TYPE,
        preferred_width=12,
        min_width=5,
    ),
    WorkItemColumn.SUMMARY: WorkItemColumnSpec(
        column=WorkItemColumn.SUMMARY,
        label="Summary",
        required=True,
        sort_key=SortKey.TITLE,
        preferred_width=36,
        min_width=12,
    ),
    WorkItemColumn.ASSIGNEE: WorkItemColumnSpec(
        column=WorkItemColumn.ASSIGNEE,
        label="Assignee",
        sort_key=SortKey.ASSIGNEE,
        preferred_width=18,
        min_width=8,
    ),
    WorkItemColumn.REPORTER: WorkItemColumnSpec(
        column=WorkItemColumn.REPORTER,
        label="Reporter",
        sort_key=SortKey.REPORTER,
        preferred_width=18,
        min_width=8,
    ),
    WorkItemColumn.PRIORITY: WorkItemColumnSpec(
        column=WorkItemColumn.PRIORITY,
        label="Priority",
        sort_key=SortKey.PRIORITY,
        preferred_width=12,
        min_width=8,
    ),
    WorkItemColumn.DUE: WorkItemColumnSpec(
        column=WorkItemColumn.DUE,
        label="Due",
        sort_key=SortKey.DUE,
        preferred_width=12,
        min_width=10,
    ),
    WorkItemColumn.LABELS: WorkItemColumnSpec(
        column=WorkItemColumn.LABELS,
        label="Labels",
        sort_key=SortKey.LABELS,
        preferred_width=20,
        min_width=8,
    ),
    WorkItemColumn.COMPONENTS: WorkItemColumnSpec(
        column=WorkItemColumn.COMPONENTS,
        label="Components",
        sort_key=SortKey.COMPONENTS,
        preferred_width=20,
        min_width=10,
    ),
    WorkItemColumn.CREATED: WorkItemColumnSpec(
        column=WorkItemColumn.CREATED,
        label="Created",
        sort_key=SortKey.CREATED,
        preferred_width=12,
        min_width=10,
    ),
}

_CARD_FIELD_TO_COLUMN = {
    "issue_type": WorkItemColumn.TYPE,
    "assignee": WorkItemColumn.ASSIGNEE,
    "priority": WorkItemColumn.PRIORITY,
    "labels": WorkItemColumn.LABELS,
    "components": WorkItemColumn.COMPONENTS,
    "due_date": WorkItemColumn.DUE,
}


def available_work_item_columns(
    card_fields: Sequence[CardFieldSpec],
    config: Mapping[str, object] | None,
    *,
    extra: Iterable[WorkItemColumn] = (),
) -> frozenset[WorkItemColumn]:
    """Return required columns plus provider-declared optional values.

    A configured field (notably Monday.com columns) is absent until its exact
    configuration key has a value.  This intentionally does not inspect task
    data: an empty board has the same column contract as a populated one.
    """
    available = set(CORE_WORK_ITEM_COLUMNS)
    for field in card_fields:
        column = _CARD_FIELD_TO_COLUMN.get(field.name.value)
        if column is None or not field.provider_key:
            continue
        if field.configuration_key and not (config and config.get(field.configuration_key)):
            continue
        available.add(column)
    available.update(extra)
    return frozenset(available)


def column_value(
    task: Task,
    column: WorkItemColumn,
    *,
    row_number: int = 0,
    status: str = "",
) -> str | int:
    """Return the normalized display value for one task and table column."""
    metadata = task.metadata
    match column:
        case WorkItemColumn.SYNC:
            return str(metadata.get("sync_status") or "")
        case WorkItemColumn.NUMBER:
            return row_number
        case WorkItemColumn.KEY:
            return str(metadata.get("key") or metadata.get("jira_key") or task.task_id)
        case WorkItemColumn.STATUS:
            return status or str(metadata.get("status") or task.column_id)
        case WorkItemColumn.TYPE:
            return str(metadata.get("issue_type") or "")
        case WorkItemColumn.SUMMARY:
            return task.title.splitlines()[-1] if task.title.splitlines() else ""
        case WorkItemColumn.ASSIGNEE:
            return str(metadata.get("assignee") or "")
        case WorkItemColumn.REPORTER:
            return str(metadata.get("reporter") or "")
        case WorkItemColumn.PRIORITY:
            return str(metadata.get("priority") or "")
        case WorkItemColumn.DUE:
            return task.due_date.isoformat() if task.due_date is not None else ""
        case WorkItemColumn.LABELS:
            labels = metadata.get("labels")
            if isinstance(labels, (list, tuple)):
                return ", ".join(str(label) for label in labels)
            return str(labels or "")
        case WorkItemColumn.COMPONENTS:
            components = metadata.get("components")
            if isinstance(components, (list, tuple)):
                return ", ".join(str(component) for component in components)
            return str(components or "")
        case WorkItemColumn.CREATED:
            return task.created_at.date().isoformat()


__all__ = [
    "CORE_WORK_ITEM_COLUMNS",
    "DEFAULT_WORK_ITEM_COLUMNS",
    "LOCAL_WORK_ITEM_COLUMNS",
    "OPTIONAL_WORK_ITEM_COLUMNS",
    "WORK_ITEM_COLUMN_SPECS",
    "WorkItemColumn",
    "WorkItemColumnSpec",
    "available_work_item_columns",
    "column_value",
]
