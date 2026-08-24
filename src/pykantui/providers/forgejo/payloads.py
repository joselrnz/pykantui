"""Pure construction of Forgejo write payloads."""

from __future__ import annotations

from datetime import date

from pykantui.api import JsonObject
from pykantui.tracker.models import CommentDraft, IssueDraft, IssueEdit


def create_comment_payload(draft: CommentDraft) -> JsonObject:
    return {"body": draft.body}


def create_issue_payload(
    draft: IssueDraft,
    *,
    label_ids: list[int],
    closed_column: str,
) -> JsonObject:
    payload: JsonObject = {"title": draft.title}
    if draft.body:
        payload["body"] = draft.body
    if draft.assignee_ids:
        payload["assignees"] = list(draft.assignee_ids)
    if label_ids:
        payload["labels"] = label_ids
    if draft.due_date:
        payload["due_date"] = _forgejo_date(draft.due_date)
    if draft.column_id == closed_column:
        payload["closed"] = True
    return payload


def update_issue_payload(edit: IssueEdit, *, open_column: str, closed_column: str) -> JsonObject:
    payload: JsonObject = {}
    if edit.title is not None:
        payload["title"] = edit.title
    if edit.body is not None:
        payload["body"] = edit.body
    if edit.assignee is not None:
        payload["assignees"] = [part.strip() for part in edit.assignee.split(",") if part.strip()]
    if "assignee" in edit.cleared:
        payload["assignees"] = []
    if edit.due_date is not None:
        payload["due_date"] = _forgejo_date(edit.due_date)
    if "due_date" in edit.cleared:
        payload["unset_due_date"] = True
    if edit.column_id in (open_column, closed_column):
        payload["state"] = "closed" if edit.column_id == closed_column else "open"
    return payload


def _forgejo_date(value: date) -> str:
    return f"{value.isoformat()}T00:00:00Z"


__all__ = ["create_comment_payload", "create_issue_payload", "update_issue_payload"]
