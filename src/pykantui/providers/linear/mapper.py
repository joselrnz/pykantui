"""Pure conversion from Linear GraphQL models to neutral tracker models."""

from __future__ import annotations

from pykantui.tracker.markup import to_markdown
from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import float_or_none, parse_date, parse_datetime

from .schemas import CommentWire, IssueWire, PageInfoWire, TeamWire, UserWire

_EMPTY_PRIORITY = {"", "none", "no priority"}


def user_to_remote(user: UserWire) -> RemoteUser:
    """Map the authenticated Linear user."""
    return RemoteUser(account_id=user.id, display_name=user.name or user.displayName, email=user.email)


def team_to_remote(team: TeamWire) -> RemoteProject:
    """Map a Linear team, which is the board container."""
    return RemoteProject(
        project_id=team.id,
        key=team.key,
        name=team.name,
        description=to_markdown(team.description or ""),
        url=f"https://linear.app/team/{team.key}",
    )


def issue_to_remote(issue: IssueWire) -> RemoteIssue:
    """Map one Linear issue."""
    priority = issue.priorityLabel.strip()
    assignee = issue.assignee or UserWire()
    creator = issue.creator or UserWire()
    return RemoteIssue(
        issue_id=issue.id,
        key=issue.identifier,
        title=issue.title,
        column_id=issue.state.id,
        body=issue.description,
        status=issue.state.name,
        priority="" if priority.lower() in _EMPTY_PRIORITY else priority,
        assignee=assignee.displayName,
        reporter=creator.displayName,
        assignee_ids=(assignee.id,) if assignee.id else (),
        reporter_id=creator.id,
        labels=tuple(label.name for label in issue.labels.nodes if label.name),
        created_at=parse_datetime(issue.createdAt),
        updated_at=parse_datetime(issue.updatedAt),
        started_at=parse_datetime(issue.startedAt),
        finished_at=parse_datetime(issue.completedAt),
        due_date=parse_date(issue.dueDate),
        parent_key=issue.parent.identifier if issue.parent else "",
        position=float_or_none(issue.sortOrder),
        url=issue.url,
    )


def comment_to_remote(comment: CommentWire, issue_id: str) -> RemoteComment:
    """Map Linear user, bot, or external authors into one display identity."""

    author = comment.user or comment.botActor or comment.externalUser or UserWire()
    return RemoteComment(
        comment_id=comment.id,
        issue_id=comment.issueId or issue_id,
        body=comment.body,
        author=author.displayName or author.name,
        author_id=author.id,
        created_at=parse_datetime(comment.createdAt),
        updated_at=parse_datetime(comment.updatedAt),
        url=comment.url,
        parent_id=comment.parentId or "",
    )


def compatibility_next_cursor(connection: dict[str, object]) -> str | None:
    """Interpret legacy cursor fixtures through the typed page model."""
    info = PageInfoWire.model_validate(connection.get("pageInfo", {}))
    return info.endCursor if info.hasNextPage else None


def compatibility_priority(issue: dict[str, object]) -> str:
    """Normalize the priority label in legacy mapping fixtures."""
    return issue_to_remote(IssueWire.model_validate(issue)).priority


__all__ = [
    "compatibility_next_cursor",
    "compatibility_priority",
    "comment_to_remote",
    "issue_to_remote",
    "team_to_remote",
    "user_to_remote",
]
