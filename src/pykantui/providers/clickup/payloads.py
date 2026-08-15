"""Pure construction of ClickUp task create and update bodies."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pykantui.api import JsonObject
from pykantui.tracker.models import CommentDraft, IssueDraft, IssueEdit, RemoteIssue

_PRIORITY_NUMBERS = {"urgent": 1, "high": 2, "normal": 3, "medium": 3, "low": 4}


def create_comment_payload(draft: CommentDraft) -> JsonObject:
    """Translate a local draft to ClickUp's plain-text comment body."""
    return {"comment_text": draft.body, "notify_all": False}


def create_task_payload(draft: IssueDraft) -> JsonObject:
    """Translate a neutral draft to ClickUp task fields."""
    payload: JsonObject = {"name": draft.title}
    if draft.body:
        payload["description"] = draft.body
    if draft.column_id:
        payload["status"] = draft.column_id
    if draft.assignee_ids:
        payload["assignees"] = list(draft.assignee_ids)
    if draft.issue_type:
        payload["custom_item_id"] = numeric_id(draft.issue_type)
    if draft.priority:
        payload["priority"] = priority_number(draft.priority)
    if draft.labels:
        payload["tags"] = list(draft.labels)
    if draft.due_date:
        payload["due_date"] = to_epoch_ms(draft.due_date)
    return payload


def update_task_payload(
    issue: RemoteIssue,
    edit: IssueEdit,
    *,
    assignee_ids: list[int] | None = None,
) -> JsonObject:
    """Translate writable neutral fields to ClickUp task fields."""
    payload: JsonObject = {}
    if edit.title is not None:
        payload["name"] = edit.title
    if edit.body is not None:
        payload["description"] = edit.body
    if edit.column_id is not None:
        payload["status"] = edit.column_id
    if edit.priority is not None:
        payload["priority"] = priority_number(edit.priority)
    if edit.assignee is not None:
        payload["assignees"] = {"add": assignee_ids or [], "rem": []}
    if edit.issue_type is not None:
        payload["custom_item_id"] = numeric_id(edit.issue_type)
    if edit.due_date is not None:
        payload["due_date"] = to_epoch_ms(edit.due_date)
    if "due_date" in edit.cleared:
        payload["due_date"] = None
    if "priority" in edit.cleared:
        payload["priority"] = None
    if "assignee" in edit.cleared:
        payload["assignees"] = {"add": [], "rem": list(issue.assignee_ids)}
    if "issue_type" in edit.cleared:
        payload["custom_item_id"] = 0
    return payload


def priority_number(value: str) -> int | None:
    """Translate ClickUp's priority label to its numeric API value."""
    return _PRIORITY_NUMBERS.get(value.strip().lower())


def numeric_id(value: str) -> int | str:
    """Use a numeric custom-item id when possible."""
    try:
        return int(value)
    except ValueError:
        return value


def to_epoch_ms(value: object) -> int | None:
    """Convert a date or timestamp to ClickUp epoch milliseconds."""
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=UTC)
    elif isinstance(value, date):
        stamp = datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        return None
    return int(stamp.timestamp() * 1000)


__all__ = ["create_comment_payload", "create_task_payload", "to_epoch_ms", "update_task_payload"]
