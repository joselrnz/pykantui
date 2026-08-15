"""Pure construction of GitHub create and update request bodies."""

from __future__ import annotations

from pykantui.api import JsonObject
from pykantui.tracker.models import CommentDraft, IssueDraft, IssueEdit, RemoteIssue


def create_comment_payload(draft: CommentDraft) -> JsonObject:
    """Translate a local draft to GitHub Markdown."""
    return {"body": draft.body}


def create_issue_payload(
    draft: IssueDraft,
    *,
    resolved_type: str | None,
    open_column: str,
    closed_column: str,
) -> JsonObject:
    """Translate a neutral draft to GitHub's issue-create JSON."""
    payload: JsonObject = {"title": draft.title}
    if draft.body:
        payload["body"] = draft.body
    if draft.assignee_ids:
        payload["assignees"] = list(draft.assignee_ids)
    if resolved_type:
        payload["type"] = resolved_type

    labels = list(draft.labels)
    if draft.column_id not in ("", open_column, closed_column):
        labels.append(draft.column_id)
    if labels:
        payload["labels"] = labels
    if draft.column_id == closed_column:
        payload["state"] = "closed"
    return payload


def update_issue_payload(
    issue: RemoteIssue,
    edit: IssueEdit,
    *,
    prefix: str,
    resolved_type: str | None,
    open_column: str,
    closed_column: str,
) -> JsonObject:
    """Translate a neutral edit to one atomic GitHub issue PATCH."""
    payload: JsonObject = {}
    if edit.title is not None:
        payload["title"] = edit.title
    if edit.body is not None:
        payload["body"] = edit.body
    if edit.assignee is not None:
        payload["assignees"] = [part.strip() for part in edit.assignee.split(",") if part.strip()]
    if edit.issue_type is not None and resolved_type:
        payload["type"] = resolved_type
    if "assignee" in edit.cleared:
        payload["assignees"] = []
    if "issue_type" in edit.cleared:
        payload["type"] = None
    if "labels" in edit.cleared:
        payload["labels"] = []

    if edit.column_id is not None:
        if edit.column_id in (open_column, closed_column):
            payload["state"] = "closed" if edit.column_id == closed_column else "open"
        else:
            base = edit.labels if edit.labels is not None else issue.labels
            keep = [label for label in base if not (prefix and label.casefold().startswith(prefix.casefold()))]
            payload["labels"] = [*keep, edit.column_id]
    elif edit.labels is not None:
        payload["labels"] = list(edit.labels)
    return payload


__all__ = ["create_comment_payload", "create_issue_payload", "update_issue_payload"]
