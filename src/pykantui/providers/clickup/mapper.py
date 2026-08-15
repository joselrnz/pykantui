"""Pure conversion from ClickUp wire models to neutral tracker models."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime

from pykantui.tracker.markup import to_markdown
from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import parse_datetime, sort_key

from .schemas import CommentWire, ListWire, PriorityWire, TaskWire, UserWire


def user_to_remote(user: UserWire) -> RemoteUser:
    """Map the authenticated ClickUp user."""
    return RemoteUser(
        account_id=str(user.id),
        display_name=user.username,
        username=user.username,
        email=user.email,
    )


def list_to_remote(entry: ListWire, where: str) -> RemoteProject:
    """Map a ClickUp list and its discovery location."""
    return RemoteProject(
        project_id=str(entry.id),
        key=entry.name,
        name=f"{where}/{entry.name}" if where else entry.name,
        description=entry.content,
        url=f"https://app.clickup.com/{entry.id}",
    )


def task_to_remote(
    task: TaskWire,
    type_names: Mapping[str, str] | None = None,
) -> RemoteIssue:
    """Map one ClickUp task."""
    priority = task.priority or PriorityWire()
    creator = task.creator or UserWire()
    assignees = [user.username for user in task.assignees if user.username]
    type_id = "" if task.custom_item_id is None else str(task.custom_item_id)
    builtin_names = {"0": "Task", "1": "Milestone"}
    issue_type = (type_names or {}).get(type_id, builtin_names.get(type_id, type_id))
    return RemoteIssue(
        issue_id=task.id,
        key=task.custom_id or task.id,
        title=task.name,
        column_id=task.status.status,
        body=to_markdown(task.text_content or task.description or ""),
        issue_type=issue_type,
        status=task.status.status,
        priority=priority.priority,
        assignee=", ".join(assignees),
        reporter=creator.username,
        assignee_ids=tuple(str(user.id) for user in task.assignees if str(user.id)),
        reporter_id=str(creator.id),
        labels=tuple(tag.name for tag in task.tags if tag.name),
        created_at=parse_datetime(epoch_to_iso(task.date_created)),
        updated_at=parse_datetime(epoch_to_iso(task.date_updated)),
        started_at=parse_datetime(epoch_to_iso(task.start_date)),
        finished_at=parse_datetime(epoch_to_iso(task.date_closed)),
        due_date=_date_of(task.due_date),
        parent_key=task.parent or "",
        position=sort_key(task.orderindex),
        url=task.url,
        extra={"issue_type_id": type_id, "team_id": str(task.team_id)},
    )


def comment_to_remote(
    comment: CommentWire,
    *,
    issue_id: str,
    issue_url: str = "",
    parent_id: str = "",
) -> RemoteComment:
    """Map a ClickUp task comment, including its canonical author and time."""
    author = comment.user or UserWire()
    body = comment.comment_text
    if body is None:
        body = "".join(part.text for part in comment.comment)
    return RemoteComment(
        comment_id=str(comment.id),
        issue_id=issue_id,
        body=body,
        author=author.username,
        author_id=str(author.id),
        created_at=parse_datetime(epoch_to_iso(comment.date)),
        updated_at=parse_datetime(epoch_to_iso(comment.date)),
        url=issue_url,
        parent_id=parent_id,
    )


def epoch_to_iso(value: object) -> str | None:
    """Convert ClickUp epoch milliseconds to an ISO timestamp."""
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _date_of(value: object) -> date | None:
    parsed = parse_datetime(epoch_to_iso(value))
    return parsed.date() if parsed else None


__all__ = ["comment_to_remote", "epoch_to_iso", "list_to_remote", "task_to_remote", "user_to_remote"]
