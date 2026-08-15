"""Pure construction of Monday item create and update variables."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pykantui.api import JsonObject, JsonValue
from pykantui.tracker.models import IssueDraft, IssueEdit

_EDIT_COLUMNS = {
    "body": "description_column",
    "assignee": "assignee_column",
    "issue_type": "type_column",
    "priority": "priority_column",
    "labels": "labels_column",
    "due_date": "due_column",
}


def create_item_variables(
    draft: IssueDraft,
    project_id: str,
    configured: Mapping[str, str],
) -> JsonObject:
    """Translate a neutral draft to Monday create-item variables."""
    values: JsonObject = {}
    candidates: tuple[tuple[str, JsonValue], ...] = (
        ("description_column", draft.body),
        (
            "assignee_column",
            {
                "personsAndTeams": [
                    {"id": as_int(item), "kind": "person"}
                    for item in draft.assignee_ids
                ]
            }
            if draft.assignee_ids
            else None,
        ),
        ("type_column", {"labels": [draft.issue_type]} if draft.issue_type else None),
        ("priority_column", {"label": draft.priority} if draft.priority else None),
        ("labels_column", {"labels": list(draft.labels)} if draft.labels else None),
        (
            "due_column",
            {"date": draft.due_date.isoformat()} if draft.due_date else None,
        ),
    )
    for config_key, value in candidates:
        column_id = configured.get(config_key, "")
        if column_id and value not in (None, ""):
            values[column_id] = value

    status_column = configured.get("status_column", "")
    group_id: str | None = None
    if draft.column_id:
        if status_column:
            values[status_column] = {"index": as_int(draft.column_id)}
        else:
            group_id = draft.column_id
    return {
        "board": project_id,
        "group": group_id,
        "name": draft.title,
        "values": json.dumps(values),
    }


def update_column_values(
    edit: IssueEdit,
    configured: Mapping[str, str],
    *,
    assignee_ids: Sequence[int | str] = (),
) -> JsonObject:
    """Build values for Monday's multi-column update mutation."""
    values: JsonObject = {}
    changes: tuple[tuple[str, JsonValue], ...] = (
        ("body", edit.body),
        (
            "assignee",
            {
                "personsAndTeams": [
                    {"id": item, "kind": "person"} for item in assignee_ids
                ]
            }
            if edit.assignee is not None
            else None,
        ),
        (
            "issue_type",
            {"labels": [edit.issue_type]} if edit.issue_type is not None else None,
        ),
        (
            "priority",
            {"label": edit.priority} if edit.priority is not None else None,
        ),
        (
            "labels",
            {"labels": list(edit.labels)} if edit.labels is not None else None,
        ),
        (
            "due_date",
            {"date": edit.due_date.isoformat()} if edit.due_date is not None else None,
        ),
    )
    changed = {
        "body": edit.body is not None,
        "assignee": edit.assignee is not None,
        "issue_type": edit.issue_type is not None,
        "priority": edit.priority is not None,
        "labels": edit.labels is not None,
        "due_date": edit.due_date is not None,
    }
    for field, value in changes:
        if changed[field]:
            values[_required_column(configured, _EDIT_COLUMNS[field])] = value
    for field in edit.cleared:
        config_key = _EDIT_COLUMNS.get(field)
        if config_key:
            values[_required_column(configured, config_key)] = None
    return values


def as_int(value: str) -> int | str:
    """Use Monday's numeric ids when a value is decimal."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _required_column(configured: Mapping[str, str], key: str) -> str:
    value = configured.get(key, "")
    if not value:
        raise ValueError(f"missing configured Monday column: {key}")
    return value


__all__ = ["as_int", "create_item_variables", "update_column_values"]
