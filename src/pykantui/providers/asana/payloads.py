"""Pure construction of Asana task create and update bodies."""

from __future__ import annotations

from pykantui.api import JsonObject
from pykantui.tracker.models import CommentDraft, IssueDraft, IssueEdit


def create_comment_payload(draft: CommentDraft) -> JsonObject:
    """Translate one append-only comment to an Asana story body."""
    return {"text": draft.body}


def create_task_payload(project_id: str, draft: IssueDraft) -> JsonObject:
    """Translate a neutral draft to Asana task fields."""
    payload: JsonObject = {"name": draft.title, "projects": [project_id]}
    if draft.body:
        payload["notes"] = draft.body
    if draft.assignee_ids:
        payload["assignee"] = draft.assignee_ids[0]
    if draft.due_date:
        payload["due_on"] = draft.due_date.isoformat()
    return payload


def update_task_payload(edit: IssueEdit, *, assignee_id: str | None = None) -> JsonObject:
    """Translate writable neutral fields to an Asana task update."""
    payload: JsonObject = {}
    if edit.title is not None:
        payload["name"] = edit.title
    if edit.body is not None:
        payload["notes"] = edit.body
    if edit.due_date is not None:
        payload["due_on"] = edit.due_date.isoformat()
    if edit.assignee is not None:
        payload["assignee"] = assignee_id or ""
    if "due_date" in edit.cleared:
        payload["due_on"] = None
    if "assignee" in edit.cleared:
        payload["assignee"] = None
    return payload


__all__ = ["create_comment_payload", "create_task_payload", "update_task_payload"]
