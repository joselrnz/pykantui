"""Pure construction of Shortcut story create and update payloads."""

from __future__ import annotations

from pykantui.api import JsonObject
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import IssueDraft, IssueEdit


def create_story_payload(draft: IssueDraft) -> JsonObject:
    """Translate a neutral draft to Shortcut story fields."""
    payload: JsonObject = {"name": draft.title}
    if draft.body:
        payload["description"] = draft.body
    if draft.column_id:
        payload["workflow_state_id"] = numeric_id(draft.column_id)
    if draft.assignee_ids:
        payload["owner_ids"] = list(draft.assignee_ids)
    if draft.issue_type:
        payload["story_type"] = draft.issue_type.casefold()
    if draft.labels:
        payload["labels"] = [{"name": label} for label in draft.labels]
    if draft.due_date:
        payload["deadline"] = draft.due_date.isoformat()
    return payload


def update_story_payload(edit: IssueEdit, *, owner_ids: list[str] | None = None) -> JsonObject:
    """Translate writable neutral fields to a Shortcut story update."""
    payload: JsonObject = {}
    if edit.title is not None:
        payload["name"] = edit.title
    if edit.body is not None:
        payload["description"] = edit.body
    if edit.column_id is not None:
        payload["workflow_state_id"] = numeric_id(edit.column_id)
    if edit.labels is not None:
        payload["labels"] = [{"name": name} for name in edit.labels]
    if edit.due_date is not None:
        payload["deadline"] = edit.due_date.isoformat()
    if edit.assignee is not None:
        payload["owner_ids"] = owner_ids or []
    if edit.issue_type is not None:
        payload["story_type"] = edit.issue_type.casefold()
    if "due_date" in edit.cleared:
        payload["deadline"] = None
    if "assignee" in edit.cleared:
        payload["owner_ids"] = []
    if "issue_type" in edit.cleared:
        raise ProviderError("Shortcut story type cannot be cleared")
    return payload


def numeric_id(value: str) -> int | str:
    """Use Shortcut's numeric representation when an id is decimal."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


__all__ = ["create_story_payload", "numeric_id", "update_story_payload"]
