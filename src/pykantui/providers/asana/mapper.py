"""Pure conversion from Asana wire models to neutral tracker models."""

from __future__ import annotations

from pykantui.tracker.markup import to_markdown
from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import parse_date, parse_datetime

from .schemas import ProjectWire, StoryWire, TaskWire, UserWire


def user_to_remote(user: UserWire) -> RemoteUser:
    """Map the authenticated Asana user."""
    return RemoteUser(account_id=user.gid, display_name=user.name, email=user.email)


def project_to_remote(project: ProjectWire) -> RemoteProject:
    """Map an Asana project discovery record."""
    workspace = project.workspace
    return RemoteProject(
        project_id=project.gid,
        key=project.name,
        name=project.name,
        description=project.notes,
        url=project.permalink_url,
        extra={
            "workspace_id": workspace.gid if workspace else "",
            "workspace_name": workspace.name if workspace else "",
        },
    )


def task_to_remote(task: TaskWire, project_id: str) -> RemoteIssue:
    """Map a task using its membership in the selected project."""
    section = _section_in_project(task, project_id)
    completed = task.completed
    assignee = task.assignee or UserWire()
    reporter = task.created_by or UserWire()
    return RemoteIssue(
        issue_id=task.gid,
        key=task.gid,
        title=task.name,
        column_id=section[0],
        body=task.notes,
        status=section[1] or ("Completed" if completed else ""),
        assignee=assignee.name,
        reporter=reporter.name,
        assignee_ids=(assignee.gid,) if assignee.gid else (),
        reporter_id=reporter.gid,
        labels=tuple(tag.name for tag in task.tags if tag.name),
        created_at=parse_datetime(task.created_at),
        updated_at=parse_datetime(task.modified_at),
        finished_at=parse_datetime(task.completed_at),
        due_date=parse_date(task.due_on),
        parent_key=task.parent.gid if task.parent else "",
        url=task.permalink_url,
        extra={"completed": completed},
    )


def story_to_remote(story: StoryWire, *, issue_id: str, issue_url: str = "") -> RemoteComment:
    """Map one Asana comment story without exposing system activity records."""
    author = story.created_by or UserWire()
    body = story.text or to_markdown(story.html_text or "")
    return RemoteComment(
        comment_id=story.gid,
        issue_id=issue_id,
        body=body,
        author=author.name,
        author_id=author.gid,
        created_at=parse_datetime(story.created_at),
        updated_at=parse_datetime(story.created_at),
        url=issue_url,
    )


def _section_in_project(task: TaskWire, project_id: str) -> tuple[str, str]:
    for membership in task.memberships:
        if membership.project.gid == project_id and membership.section is not None:
            return membership.section.gid, membership.section.name
    first = next((membership.section for membership in task.memberships if membership.section is not None), None)
    return (first.gid, first.name) if first else ("", "")


__all__ = ["project_to_remote", "story_to_remote", "task_to_remote", "user_to_remote"]
