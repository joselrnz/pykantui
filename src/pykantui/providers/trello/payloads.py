"""Pure construction of Trello card create and update parameters."""

from __future__ import annotations

from pykantui.api import QueryValue
from pykantui.tracker.models import IssueDraft, IssueEdit


def create_card_params(draft: IssueDraft, *, label_ids: list[str]) -> dict[str, QueryValue]:
    """Translate a neutral draft to Trello card parameters."""
    params: dict[str, QueryValue] = {"name": draft.title}
    if draft.body:
        params["desc"] = draft.body
    if draft.column_id:
        params["idList"] = draft.column_id
    if draft.assignee_ids:
        params["idMembers"] = ",".join(draft.assignee_ids)
    if label_ids:
        params["idLabels"] = ",".join(label_ids)
    if draft.due_date:
        params["due"] = draft.due_date.isoformat()
    return params


def update_card_params(
    edit: IssueEdit,
    *,
    member_ids: list[str] | None = None,
    label_ids: list[str] | None = None,
) -> dict[str, QueryValue]:
    """Translate writable neutral fields to Trello card parameters."""
    params: dict[str, QueryValue] = {}
    if edit.title is not None:
        params["name"] = edit.title
    if edit.body is not None:
        params["desc"] = edit.body
    if edit.column_id is not None:
        params["idList"] = edit.column_id
    if edit.due_date is not None:
        params["due"] = edit.due_date.isoformat()
    if edit.assignee is not None:
        params["idMembers"] = ",".join(member_ids or [])
    if edit.labels is not None:
        params["idLabels"] = ",".join(label_ids or [])
    if "due_date" in edit.cleared:
        params["due"] = "null"
    if "assignee" in edit.cleared:
        params["idMembers"] = ""
    if "labels" in edit.cleared:
        params["idLabels"] = ""
    return params


__all__ = ["create_card_params", "update_card_params"]
