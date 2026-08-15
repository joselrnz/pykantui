"""Pure conversion from Shortcut wire models to neutral tracker models."""

from __future__ import annotations

from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import parse_date, parse_datetime, sort_key

from .schemas import CommentWire, MemberWire, StoryWire, WorkflowWire


def member_to_remote(member: MemberWire) -> RemoteUser:
    """Map the authenticated Shortcut member."""
    return RemoteUser(
        account_id=str(member.id),
        display_name=member.mention_name or member.name,
    )


def workflow_to_remote(workflow: WorkflowWire) -> RemoteProject:
    """Map a workflow, which is Shortcut's board container."""
    return RemoteProject(
        project_id=str(workflow.id),
        key=workflow.name,
        name=workflow.name,
        description=workflow.description,
        extra={"team_id": workflow.team_id},
    )


def story_to_remote(
    story: StoryWire,
    states: dict[str, str],
    members: dict[str, str] | None = None,
) -> RemoteIssue:
    """Map a Shortcut story and resolve its owner ids to display names."""
    state_id = str(story.workflow_state_id)
    owner_ids = tuple(str(value) for value in story.owner_ids)
    owners = [(members or {}).get(owner, "") for owner in owner_ids]
    return RemoteIssue(
        issue_id=str(story.id),
        key=f"sc-{story.id}",
        title=story.name,
        column_id=state_id,
        body=story.description,
        status=states.get(state_id, ""),
        issue_type=story.story_type,
        assignee=", ".join(name for name in owners if name),
        reporter=(members or {}).get(str(story.requested_by_id), ""),
        assignee_ids=owner_ids,
        reporter_id=str(story.requested_by_id),
        labels=tuple(label.name for label in story.labels if label.name),
        created_at=parse_datetime(story.created_at),
        updated_at=parse_datetime(story.updated_at),
        started_at=parse_datetime(story.started_at),
        finished_at=parse_datetime(story.completed_at),
        due_date=parse_date(story.deadline),
        parent_key=str(story.epic_id or ""),
        position=sort_key(story.position),
        url=story.app_url,
    )


def comment_to_remote(
    comment: CommentWire,
    issue_id: str,
    members: dict[str, str],
) -> RemoteComment:
    """Map a Shortcut comment with the already-cached member directory."""

    author_id = str(comment.author_id) if comment.author_id is not None else ""
    return RemoteComment(
        comment_id=str(comment.id),
        issue_id=str(comment.story_id or issue_id),
        body=comment.text or "",
        author=members.get(author_id, ""),
        author_id=author_id,
        created_at=parse_datetime(comment.created_at),
        updated_at=parse_datetime(comment.updated_at),
        url=comment.app_url,
        parent_id=str(comment.parent_id or ""),
        deleted=comment.deleted,
    )


__all__ = ["comment_to_remote", "member_to_remote", "story_to_remote", "workflow_to_remote"]
