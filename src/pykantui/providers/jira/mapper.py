"""Pure conversion from Jira wire models to neutral tracker models."""

from __future__ import annotations

from pykantui.tracker.markup import to_markdown
from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import parse_date, parse_datetime

from .schemas import CommentWire, IssueWire, ProjectWire, UserWire


def user_to_remote(user: UserWire) -> RemoteUser:
    """Map the authenticated Jira account."""
    return RemoteUser(
        account_id=user.accountId,
        display_name=user.displayName,
        email=user.emailAddress,
    )


def project_to_remote(project: ProjectWire, base_url: str) -> RemoteProject:
    """Map one Jira project."""
    return RemoteProject(
        project_id=project.id,
        key=project.key,
        name=project.name,
        url=f"{base_url}/browse/{project.key}",
        extra={"style": project.style, "type": project.projectTypeKey},
    )


def issue_to_remote(issue: IssueWire, base_url: str) -> RemoteIssue:
    """Map one Jira issue including ADF/plain-text description handling."""
    fields = issue.fields
    assignee = fields.assignee or UserWire()
    reporter = fields.reporter or UserWire()
    category = fields.status.statusCategory.key if fields.status.statusCategory else ""
    return RemoteIssue(
        issue_id=issue.id,
        key=issue.key,
        title=fields.summary,
        column_id=fields.status.id,
        body=to_markdown(fields.description),
        issue_type=fields.issuetype.name if fields.issuetype else "",
        status=fields.status.name,
        priority=fields.priority.name if fields.priority else "",
        assignee=assignee.displayName,
        reporter=reporter.displayName,
        assignee_ids=(assignee.accountId,) if assignee.accountId else (),
        reporter_id=reporter.accountId,
        labels=tuple(fields.labels),
        components=tuple(component.name for component in fields.components if component.name),
        created_at=parse_datetime(fields.created),
        updated_at=parse_datetime(fields.updated),
        finished_at=parse_datetime(fields.resolutiondate),
        due_date=parse_date(fields.duedate),
        parent_key=fields.parent.key if fields.parent else "",
        url=f"{base_url}/browse/{issue.key}" if issue.key else "",
        extra={"status_category": category},
    )


def comment_to_remote(comment: CommentWire, issue_id: str) -> RemoteComment:
    """Map one Jira ADF comment without exposing account metadata."""

    return RemoteComment(
        comment_id=comment.id,
        issue_id=issue_id,
        body=to_markdown(comment.body),
        author=comment.author.displayName,
        author_id=comment.author.accountId,
        created_at=parse_datetime(comment.created),
        updated_at=parse_datetime(comment.updated),
        url=comment.self,
    )


__all__ = ["comment_to_remote", "issue_to_remote", "project_to_remote", "user_to_remote"]
