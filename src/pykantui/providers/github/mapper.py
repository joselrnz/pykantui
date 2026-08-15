"""Pure conversion from GitHub wire models to neutral tracker models."""

from __future__ import annotations

from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import parse_datetime

from .schemas import CommentWire, IssueTypeWire, IssueWire, RepositoryWire, UserWire


def user_to_remote(user: UserWire) -> RemoteUser:
    """Map the authenticated GitHub account."""
    return RemoteUser(
        account_id=str(user.id),
        display_name=user.name or user.login,
        username=user.login,
        email=user.email or "",
    )


def repository_to_remote(repository: RepositoryWire) -> RemoteProject:
    """Map a repository to pykantui's neutral project model."""
    return RemoteProject(
        project_id=repository.full_name,
        key=repository.name,
        name=repository.full_name,
        owner=repository.owner.login,
        description=repository.description or "",
        url=repository.html_url or "",
        extra={"private": repository.private},
    )


def issue_to_remote(
    issue: IssueWire,
    repository: str,
    prefix: str,
    *,
    open_column: str,
    closed_column: str,
) -> RemoteIssue:
    """Map one GitHub issue, including label-backed board status."""
    labels = [label.name for label in issue.labels if label.name]
    status_label = next(
        (label for label in labels if prefix and label.casefold().startswith(prefix.casefold())),
        "",
    )
    closed = issue.state == "closed"
    if status_label:
        column_id = status_label
        status = status_label[len(prefix) :].strip() or status_label
    else:
        column_id = closed_column if closed else open_column
        status = "Closed" if closed else "Open"

    number = issue.number
    reporter = issue.user or UserWire()
    return RemoteIssue(
        issue_id=str(issue.id),
        key=f"{repository.split('/')[-1]}#{number}" if number is not None else str(issue.id),
        title=issue.title or "",
        column_id=column_id,
        body=issue.body or "",
        issue_type=_issue_type_name(issue),
        status=status,
        assignee=", ".join(user.login for user in issue.assignees if user.login),
        reporter=reporter.login,
        assignee_ids=tuple(str(user.id) for user in issue.assignees if str(user.id)),
        reporter_id=str(reporter.id) if str(reporter.id) else "",
        labels=tuple(label for label in labels if label != status_label),
        created_at=parse_datetime(issue.created_at),
        updated_at=parse_datetime(issue.updated_at),
        finished_at=parse_datetime(issue.closed_at),
        url=issue.html_url or "",
        extra={"number": number, "state": issue.state},
    )


def comment_to_remote(comment: CommentWire, *, issue_id: str) -> RemoteComment:
    """Map one issue-conversation comment to the shared comment model."""
    author = comment.user or UserWire()
    return RemoteComment(
        comment_id=str(comment.id),
        issue_id=issue_id,
        body=comment.body or "",
        author=author.login,
        author_id=str(author.id),
        created_at=parse_datetime(comment.created_at),
        updated_at=parse_datetime(comment.updated_at),
        url=comment.html_url or "",
    )


def _issue_type_name(issue: IssueWire) -> str:
    value = issue.type
    return value.name if isinstance(value, IssueTypeWire) else str(value or "")


__all__ = ["comment_to_remote", "issue_to_remote", "repository_to_remote", "user_to_remote"]
