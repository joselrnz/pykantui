"""Pure Forgejo-to-neutral model conversion."""

from __future__ import annotations

from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import parse_date, parse_datetime

from .schemas import CommentWire, IssueWire, RepositoryWire, UserWire


def user_to_remote(user: UserWire) -> RemoteUser:
    return RemoteUser(
        account_id=user.login,
        display_name=user.full_name or user.login,
        username=user.login,
        email=user.email or "",
    )


def repository_to_remote(repository: RepositoryWire) -> RemoteProject:
    return RemoteProject(
        project_id=repository.full_name,
        key=repository.name,
        name=repository.full_name,
        owner=repository.owner.login,
        description=repository.description or "",
        url=repository.html_url or "",
        extra={
            "id": str(repository.id),
            "private": repository.private,
            "archived": repository.archived,
        },
    )


def issue_to_remote(
    issue: IssueWire,
    repository: str,
    prefix: str,
    *,
    open_column: str,
    closed_column: str,
) -> RemoteIssue:
    labels = [label.name for label in issue.labels if label.name]
    status_label = next(
        (label for label in labels if prefix and label.casefold().startswith(prefix.casefold())),
        "",
    )
    closed = issue.state.casefold() == "closed"
    if status_label:
        column_id = status_label
        status = status_label[len(prefix) :].strip() or status_label
    else:
        column_id = closed_column if closed else open_column
        status = "Closed" if closed else "Open"

    reporter = issue.user or UserWire()
    number = issue.number
    return RemoteIssue(
        issue_id=str(issue.id),
        key=f"{repository.split('/')[-1]}#{number}" if number is not None else str(issue.id),
        title=issue.title or "",
        column_id=column_id,
        body=issue.body or "",
        status=status,
        assignee=", ".join(user.login for user in issue.assignees if user.login),
        reporter=reporter.login,
        assignee_ids=tuple(user.login for user in issue.assignees if user.login),
        reporter_id=reporter.login,
        labels=tuple(label for label in labels if label != status_label),
        created_at=parse_datetime(issue.created_at),
        updated_at=parse_datetime(issue.updated_at),
        finished_at=parse_datetime(issue.closed_at),
        due_date=parse_date(issue.due_date),
        url=issue.html_url or "",
        extra={"number": number, "state": issue.state},
    )


def comment_to_remote(comment: CommentWire, *, issue_id: str) -> RemoteComment:
    author = comment.user or UserWire()
    return RemoteComment(
        comment_id=str(comment.id),
        issue_id=issue_id,
        body=comment.body or "",
        author=author.login,
        author_id=author.login,
        created_at=parse_datetime(comment.created_at),
        updated_at=parse_datetime(comment.updated_at),
        url=comment.html_url or "",
    )


__all__ = ["comment_to_remote", "issue_to_remote", "repository_to_remote", "user_to_remote"]
