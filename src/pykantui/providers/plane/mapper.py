"""Pure conversion from Plane wire models to neutral tracker models."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, urlsplit

from pykantui.tracker.markup import to_markdown
from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject
from pykantui.tracker.util import float_or_none, parse_date, parse_datetime

from .schemas import CommentWire, ProjectWire, WorkItemWire

_EMPTY_PRIORITY = {"none", "null", ""}
_CLOUD_API_HOST = "api.plane.so"
_CLOUD_WEB_ORIGIN = "https://app.plane.so"


def plane_web_origin(api_base_url: str) -> str:
    """Derive Plane's browser origin from its configured API origin.

    Plane Cloud deliberately separates ``api.plane.so`` from
    ``app.plane.so``. Self-hosted installations normally serve their API and
    web app on the configured custom origin, so preserving that origin avoids
    sending those users to Plane Cloud.
    """
    try:
        parsed = urlsplit(api_base_url.strip())
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    if parsed.scheme == "https" and parsed.hostname.casefold() == _CLOUD_API_HOST and port in (None, 443):
        return _CLOUD_WEB_ORIGIN
    return f"{parsed.scheme}://{parsed.netloc}"


def _plane_web_url(api_base_url: str, *parts: str, trailing_slash: bool = False) -> str:
    origin = plane_web_origin(api_base_url)
    if not origin:
        return ""
    path = "/".join(quote(part.strip("/"), safe="") for part in parts)
    return f"{origin}/{path}{'/' if trailing_slash else ''}"


def project_to_remote(
    project: ProjectWire,
    workspace: str,
    *,
    api_base_url: str = "https://api.plane.so",
) -> RemoteProject:
    """Map one Plane project."""
    return RemoteProject(
        project_id=project.id,
        key=project.identifier or project.name,
        name=project.name,
        description=project.description,
        url=_plane_web_url(
            api_base_url,
            workspace,
            "projects",
            project.id,
            "issues",
            trailing_slash=True,
        ),
        extra={"identifier": project.identifier},
    )


def work_item_to_remote(
    item: WorkItemWire,
    *,
    workspace: str,
    project_id: str,
    identifier: str,
    states: dict[str, str],
    members: dict[str, str],
    labels: list[str],
    api_base_url: str = "https://api.plane.so",
) -> RemoteIssue:
    """Map one Plane work item with already-resolved directory values."""
    key = f"{identifier}-{item.sequence_id}" if identifier and item.sequence_id is not None else item.id
    assignees = [members.get(member_id, "") for member_id in item.assignees]
    type_id = item.type_id or ""
    return RemoteIssue(
        issue_id=item.id,
        key=key,
        title=item.name,
        column_id=item.state,
        body=to_markdown(item.description_html or item.description_stripped),
        status=states.get(item.state, item.state_group),
        priority="" if item.priority.lower() in _EMPTY_PRIORITY else item.priority,
        assignee=", ".join(name for name in assignees if name),
        reporter=members.get(item.created_by, ""),
        assignee_ids=tuple(item.assignees),
        reporter_id=item.created_by,
        labels=tuple(labels),
        created_at=parse_datetime(item.created_at),
        updated_at=parse_datetime(item.updated_at),
        started_at=parse_datetime(item.start_date),
        finished_at=parse_datetime(item.completed_at),
        due_date=parse_date(item.target_date),
        parent_key=item.parent or "",
        position=float_or_none(item.sort_order),
        url=_plane_web_url(
            api_base_url,
            workspace,
            "projects",
            project_id,
            "issues",
            item.id,
        ),
        extra={
            "state_group": item.state_group,
            "estimate": item.estimate_point,
            "issue_type_id": type_id,
        },
    )


def comment_to_remote(
    comment: CommentWire,
    issue_id: str,
    members: Mapping[str, str] | None = None,
) -> RemoteComment:
    """Map one Plane comment while preserving visibility and deletion state."""

    actor = comment.actor
    actor_id = actor if isinstance(actor, str) else (actor.id if actor else comment.created_by)
    author = "" if isinstance(actor, str) or actor is None else (actor.display_name or actor.email)
    if not author and actor_id:
        author = (members or {}).get(actor_id, "")
    return RemoteComment(
        comment_id=comment.id,
        issue_id=issue_id,
        body=comment.comment_stripped or to_markdown(comment.comment_html),
        author=author,
        author_id=actor_id,
        created_at=parse_datetime(comment.created_at),
        updated_at=parse_datetime(comment.updated_at or comment.edited_at),
        parent_id=comment.parent or "",
        deleted=bool(comment.deleted_at),
        extra={"access": comment.access} if comment.access else {},
    )


__all__ = ["comment_to_remote", "plane_web_origin", "project_to_remote", "work_item_to_remote"]
