"""Pure construction of Linear issue create and update inputs."""

from __future__ import annotations

from pykantui.api import JsonObject
from pykantui.tracker.models import IssueDraft, IssueEdit

_PRIORITIES = {"urgent": 1, "high": 2, "medium": 3, "normal": 3, "low": 4}


def create_issue_input(draft: IssueDraft, team_id: str, *, label_ids: list[str]) -> JsonObject:
    """Translate a neutral draft to Linear IssueCreateInput."""
    payload: JsonObject = {"title": draft.title, "teamId": team_id}
    if draft.body:
        payload["description"] = draft.body
    if draft.column_id:
        payload["stateId"] = draft.column_id
    if draft.assignee_ids:
        payload["assigneeId"] = draft.assignee_ids[0]
    if label_ids:
        payload["labelIds"] = label_ids
    if draft.priority:
        payload["priority"] = priority_value(draft.priority)
    if draft.due_date:
        payload["dueDate"] = draft.due_date.isoformat()
    return payload


def update_issue_input(
    edit: IssueEdit,
    *,
    assignee_id: str | None = None,
    label_ids: list[str] | None = None,
) -> JsonObject:
    """Translate writable neutral fields to Linear IssueUpdateInput."""
    payload: JsonObject = {}
    if edit.title is not None:
        payload["title"] = edit.title
    if edit.body is not None:
        payload["description"] = edit.body
    if edit.column_id is not None:
        payload["stateId"] = edit.column_id
    if edit.due_date is not None:
        payload["dueDate"] = edit.due_date.isoformat()
    if edit.assignee is not None:
        payload["assigneeId"] = assignee_id
    if edit.labels is not None:
        payload["labelIds"] = label_ids or []
    if edit.priority is not None:
        payload["priority"] = priority_value(edit.priority)
    if "due_date" in edit.cleared:
        payload["dueDate"] = None
    if "assignee" in edit.cleared:
        payload["assigneeId"] = None
    if "labels" in edit.cleared:
        payload["labelIds"] = []
    if "priority" in edit.cleared:
        payload["priority"] = 0
    return payload


def priority_value(value: str) -> int:
    """Translate a Linear priority label to its numeric API value."""
    return _PRIORITIES.get(value.strip().casefold(), 0)


__all__ = ["create_issue_input", "update_issue_input"]
