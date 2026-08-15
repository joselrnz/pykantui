"""Pure construction of Jira issue create and update field bodies."""

from __future__ import annotations

from pykantui.api import JsonObject, JsonValue
from pykantui.tracker.models import IssueDraft, IssueEdit, IssueType


def create_issue_fields(project_id: str, draft: IssueDraft, issue_type: IssueType | None) -> JsonObject:
    """Translate a neutral draft to Jira issue fields."""
    fields: JsonObject = {"project": {"id": project_id}, "summary": draft.title}
    if issue_type is not None:
        fields["issuetype"] = {"id": issue_type.type_id} if issue_type.type_id else {"name": issue_type.name}
    if draft.body:
        fields["description"] = draft.body
    if draft.priority:
        fields["priority"] = {"name": draft.priority}
    if draft.labels:
        fields["labels"] = list(draft.labels)
    if draft.components:
        fields["components"] = [{"name": name} for name in draft.components]
    if draft.due_date:
        fields["duedate"] = draft.due_date.isoformat()
    if draft.parent_key:
        fields["parent"] = {"key": draft.parent_key}
    if draft.assignee_ids:
        fields["assignee"] = {"accountId": draft.assignee_ids[0]}
    return fields


def update_issue_fields(edit: IssueEdit, *, assignee_id: str | None = None) -> JsonObject:
    """Translate writable neutral fields to Jira v2 issue fields."""
    fields: JsonObject = {}
    if edit.title is not None:
        fields["summary"] = edit.title
    if edit.body is not None:
        fields["description"] = edit.body
    if edit.due_date is not None:
        fields["duedate"] = edit.due_date.isoformat()
    if edit.labels is not None:
        fields["labels"] = list(edit.labels)
    if edit.assignee is not None:
        fields["assignee"] = {"accountId": assignee_id or ""}
    if edit.priority is not None:
        fields["priority"] = {"name": edit.priority}
    if edit.issue_type is not None:
        fields["issuetype"] = {"name": edit.issue_type}
    if edit.components is not None:
        fields["components"] = [{"name": name} for name in edit.components]
    for name in edit.cleared:
        if name == "due_date":
            fields["duedate"] = None
        elif name == "labels":
            fields["labels"] = []
        elif name == "assignee":
            fields["assignee"] = None
        elif name == "priority":
            fields["priority"] = None
        elif name == "components":
            fields["components"] = []
    return fields


def comment_document(body: str) -> JsonObject:
    """Encode local Markdown as literal Jira ADF text.

    Comment drafts are treated as text, never interpreted as HTML. Newlines
    become ADF hard breaks so Jira preserves them without accepting markup
    injection from a Markdown file.
    """

    content: list[JsonValue] = []
    lines = body.split("\n")
    for index, line in enumerate(lines):
        if line:
            content.append({"type": "text", "text": line})
        if index < len(lines) - 1:
            content.append({"type": "hardBreak"})
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": content}],
    }


__all__ = ["comment_document", "create_issue_fields", "update_issue_fields"]
