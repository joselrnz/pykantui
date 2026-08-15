"""Build outbound sync plans without mutating local or provider state."""

from __future__ import annotations

from pykantui.tracker.base import Provider
from pykantui.tracker.models import IssueEdit, RemoteIssue, RemoteProject
from pykantui.workspace import layout, markdown
from pykantui.workspace.disk import OnDisk
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.models import InvalidCard, PendingCommentPush, PendingPush, SyncPlan
from pykantui.workspace.state import SyncState


def build_plan(
    provider: Provider,
    project: RemoteProject,
    on_disk: dict[str, OnDisk],
    state: SyncState,
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
    check_remote: bool = True,
) -> SyncPlan:
    """Return the local edits that are eligible for an outbound sync.

    Only locally edited cards are refreshed from the provider. This keeps
    conflict detection accurate without turning every preview into a complete
    project pull.
    """
    plan = SyncPlan()
    columns = layout.folder_index(provider.columns(project.project_id), column_style)
    writable = provider.editable_card_fields()
    duplicate_draft_errors = _duplicate_comment_draft_errors(on_disk)

    for issue_id, entry in sorted(on_disk.items()):
        if not entry.file.valid:
            plan.invalid.append(
                InvalidCard(issue_id=issue_id, filename=entry.path.name, errors=entry.file.errors)
            )
            continue
        if errors := duplicate_draft_errors.get(issue_id):
            plan.invalid.append(
                InvalidCard(issue_id=issue_id, filename=entry.path.name, errors=errors)
            )
            continue
        previous = state.get(issue_id)
        if previous is None:
            continue

        plan.comment_pushes.extend(
            PendingCommentPush(key=previous.display_key(), previous=previous, draft=draft)
            for draft in entry.file.comment_drafts
        )

        column = columns.get(entry.column_name)
        edit = markdown.edit_from(
            entry.file,
            column_id=column.column_id if column else previous.column_id,
            previous=previous,
        )
        if edit.is_empty():
            continue

        pending = PendingPush(key=previous.display_key(), previous=previous, edit=edit)
        if edit.unsupported(writable):
            pending.unchecked = True
            plan.pushes.append(pending)
            continue

        if check_remote:
            remote = provider.get_issue(project.project_id, previous)
            if remote is None:
                pending.unchecked = True
            else:
                pending.remote = remote
                pending.conflict = edits_conflict(pending.edit, previous, remote)
        else:
            pending.unchecked = True
        plan.pushes.append(pending)

    return plan


def _duplicate_comment_draft_errors(
    on_disk: dict[str, OnDisk],
) -> dict[str, tuple[str, ...]]:
    """Return cross-card draft-id collisions that make both cards unsafe."""
    owners: dict[str, list[tuple[str, str]]] = {}
    for issue_id, entry in sorted(on_disk.items()):
        if not entry.file.valid:
            continue
        for draft in entry.file.comment_drafts:
            owners.setdefault(draft.local_id, []).append((issue_id, entry.path.name))

    errors: dict[str, list[str]] = {}
    for local_id, locations in owners.items():
        if len(locations) < 2:
            continue
        for issue_id, filename in locations:
            others = ", ".join(
                other_filename
                for other_issue_id, other_filename in locations
                if other_issue_id != issue_id
            )
            errors.setdefault(issue_id, []).append(
                f"duplicate comment draft id {local_id!r} also appears in {others or filename}"
            )
    return {issue_id: tuple(messages) for issue_id, messages in errors.items()}


def edits_conflict(local: IssueEdit, before: RemoteIssue, now: RemoteIssue) -> bool:
    """Return whether local and remote changed the same field differently."""
    remote = IssueEdit.changed(before, now)
    overlap = set(local.touched()) & set(remote.touched())
    for field_name in overlap:
        wanted = getattr(local, field_name)
        if field_name in local.cleared:
            wanted = () if field_name in {"labels", "components"} else None if field_name == "due_date" else ""
        if wanted != getattr(now, field_name):
            return True
    return False
