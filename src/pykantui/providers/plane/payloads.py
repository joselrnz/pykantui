"""Pure construction of Plane work-item create and update bodies."""

from __future__ import annotations

from pykantui.api import JsonObject
from pykantui.tracker.models import IssueDraft, IssueEdit


def create_work_item_payload(draft: IssueDraft, *, label_ids: list[str]) -> JsonObject:
    """Translate a neutral draft to Plane work-item fields."""
    body: JsonObject = {"name": draft.title}
    if draft.body:
        body["description_html"] = draft.body
    if draft.column_id:
        body["state"] = draft.column_id
    if draft.priority:
        body["priority"] = draft.priority.lower()
    if draft.due_date:
        body["target_date"] = draft.due_date.isoformat()
    if draft.assignee_ids:
        body["assignees"] = list(draft.assignee_ids)
    if label_ids:
        body["labels"] = label_ids
    return body


def update_work_item_payload(
    edit: IssueEdit,
    *,
    member_ids: list[str] | None = None,
    label_ids: list[str] | None = None,
) -> JsonObject:
    """Translate writable neutral fields to Plane work-item fields."""
    body: JsonObject = {}
    if edit.title is not None:
        body["name"] = edit.title
    if edit.body is not None:
        body["description_html"] = edit.body
    if edit.column_id is not None:
        body["state"] = edit.column_id
    if edit.priority is not None:
        body["priority"] = edit.priority.lower()
    if edit.due_date is not None:
        body["target_date"] = edit.due_date.isoformat()
    if edit.assignee is not None:
        body["assignees"] = member_ids or []
    if edit.labels is not None:
        body["labels"] = label_ids or []
    for name in edit.cleared:
        if name == "due_date":
            body["target_date"] = None
        elif name == "priority":
            body["priority"] = "none"
        elif name == "assignee":
            body["assignees"] = []
        elif name == "labels":
            body["labels"] = []
    return body


__all__ = ["create_work_item_payload", "update_work_item_payload"]
