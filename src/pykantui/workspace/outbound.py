"""Translate approved local Markdown changes into provider operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pykantui.api.errors import PayloadError
from pykantui.config.paths import write_text_atomic
from pykantui.i18n import translate as _
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import (
    NotFoundError,
    ProviderError,
    TransportError,
    UnsupportedError,
)
from pykantui.tracker.models import CommentDraft, IssueDraft, IssueEdit, RemoteComment, RemoteIssue, RemoteProject
from pykantui.tracker.util import parse_date
from pykantui.workspace import layout, markdown
from pykantui.workspace.disk import OnDisk
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.models import ConflictResolution, SyncPlan, SyncReport
from pykantui.workspace.paths import ensure_workspace_path
from pykantui.workspace.pending import PendingCommentJournal, PendingCommentState, PendingCreateJournal
from pykantui.workspace.progress import ProgressCounter, tracked_items
from pykantui.workspace.state import SyncState


def pending_drafts(provider: Provider, on_disk: dict[str, OnDisk], report: SyncReport) -> dict[str, OnDisk]:
    """Return draft files the provider can create, reporting unsupported ones."""
    from pykantui.commands.new import is_draft  # noqa: PLC0415 - avoids a command/workspace cycle

    drafts = {key: entry for key, entry in on_disk.items() if is_draft(key) and entry.file.valid}
    if drafts and not provider.spec.capabilities.create_issues:
        for entry in drafts_in_name_order(drafts):
            report.skipped.append((entry.path.name, f"{provider.spec.label} cannot create issues"))
        return {}
    return drafts


def drafts_in_name_order(drafts: dict[str, OnDisk]) -> list[OnDisk]:
    """Return drafts in a stable, user-visible creation order."""
    return sorted(drafts.values(), key=lambda item: item.path.name)


def draft_title(entry: OnDisk) -> str:
    """Return the explicit draft title or its filename fallback."""
    return str(entry.file.front.get("title", "") or entry.path.stem)


def draft_signature(entry: OnDisk) -> str:
    """Fingerprint every draft value that can influence provider creation."""
    material = json.dumps(
        {"column": entry.column_name, "front": entry.file.front, "source": entry.file.source},
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def comment_signature(draft: CommentDraft) -> str:
    """Fingerprint a comment without copying its body into runtime metadata."""
    material = json.dumps(
        {
            "local_id": draft.local_id,
            "issue_id": draft.issue_id,
            "body": draft.body,
            "created_at": draft.created_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class CommentApplyResult:
    """Confirmed comment writes waiting for one atomic Markdown rewrite."""

    journal: PendingCommentJournal
    journal_path: Path
    posted: dict[str, list[RemoteComment]] = field(default_factory=dict)
    confirmed_remote_ids: dict[str, str] = field(default_factory=dict)
    draft_issue_ids: dict[str, str] = field(default_factory=dict)


def apply_comment_plan(
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    plan: SyncPlan,
    on_disk: dict[str, OnDisk],
    report: SyncReport,
    *,
    retry_ambiguous: bool = False,
    progress: ProgressCounter | None = None,
) -> CommentApplyResult:
    """Post approved append-only comments once, guarded by a durable journal."""
    journal_path = ensure_workspace_path(workspace, layout.pending_comments_file(workspace))
    journal = PendingCommentJournal.load(journal_path)
    result = CommentApplyResult(journal=journal, journal_path=journal_path)

    for item in tracked_items(plan.comment_pushes, progress, lambda pending: pending.key):
        draft = item.draft
        result.draft_issue_ids[draft.local_id] = item.previous.issue_id
        entry = on_disk.get(item.previous.issue_id)
        filename = entry.path.name if entry is not None else f"{item.key}.md"
        if not provider.spec.capabilities.create_comments:
            report.skipped.append((filename, f"{provider.spec.label} cannot create comments"))
            report.held.append(filename)
            continue

        previous_attempt = journal.attempts.get(draft.local_id)
        if previous_attempt is not None:
            if previous_attempt.state is PendingCommentState.CONFIRMED:
                result.confirmed_remote_ids[draft.local_id] = previous_attempt.remote_id
                continue
            if not retry_ambiguous:
                changed = previous_attempt.signature != comment_signature(draft)
                suffix = " and the draft changed afterward" if changed else ""
                report.skipped.append(
                    (
                        filename,
                        f"previous comment outcome is unknown{suffix}; not retried automatically",
                    )
                )
                report.held.append(filename)
                continue

        journal.begin(
            journal_path,
            draft.local_id,
            issue_id=item.previous.issue_id,
            filename=filename,
            signature=comment_signature(draft),
        )
        try:
            posted = provider.create_comment(project.project_id, item.previous, draft)
        except (TransportError, PayloadError, NotFoundError) as error:
            report.skipped.append(
                (
                    filename,
                    "comment outcome is unknown because the provider did not return "
                    "a trustworthy confirmation: "
                    + str(error).splitlines()[0],
                )
            )
            report.held.append(filename)
            continue
        except (ProviderError, UnsupportedError) as error:
            journal.resolve(journal_path, draft.local_id)
            report.skipped.append((filename, str(error).splitlines()[0]))
            report.held.append(filename)
            continue
        finally:
            # The request may have reached the provider even when response
            # parsing or read-back failed. Never let a pre-POST cached thread
            # conceal a possibly accepted append.
            provider.invalidate_comment_cache(
                project.project_id,
                item.previous.issue_id or item.previous.key,
            )

        remote_id = posted.comment_id.strip()
        if not remote_id:
            report.skipped.append(
                (
                    filename,
                    "comment outcome is unknown because the provider returned no comment id",
                )
            )
            report.held.append(filename)
            continue

        journal.confirm(journal_path, draft.local_id, remote_id=remote_id)
        result.posted.setdefault(item.previous.issue_id, []).append(posted)
        result.confirmed_remote_ids[draft.local_id] = remote_id
        report.commented.append(item.key)

    return result


def draft_preview(entry: OnDisk, creatable_fields: tuple[str, ...]) -> str:
    """Describe only fields the selected provider declares as creatable."""
    front = entry.file.front
    allowed = set(creatable_fields)
    fields: list[str] = []
    if "title" in allowed:
        fields.append(_("Summary"))
    if entry.file.source and "body" in allowed:
        fields.append(_("Description"))
    if front.get("type") and "issue_type" in allowed:
        fields.append(_("Type"))
    if "column_id" in allowed:
        fields.append(_("Status"))
    for key, field_name, label in (
        ("assignee", "assignee", _("Assignee")),
        ("priority", "priority", _("Priority")),
        ("labels", "labels", _("Labels")),
        ("components", "components", _("Components")),
        ("due", "due_date", _("Due Date")),
        ("parent", "parent_key", _("Parent")),
    ):
        if front.get(key) and field_name in allowed:
            fields.append(label)
    issue_type = (
        str(front.get("type", "") or _("provider default"))
        if "issue_type" in allowed
        else _("provider default")
    )
    column = (
        str(front.get("status", "") or entry.column_name)
        if "column_id" in allowed
        else _("provider default")
    )
    return (
        _("type: {value}").format(value=issue_type)
        + "\n"
        + _("column: {value}").format(value=column)
        + "\n"
        + _("fields: {value}").format(value=", ".join(fields))
    )


def create_drafts(
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    drafts: dict[str, OnDisk],
    state: SyncState,
    report: SyncReport,
    column_style: ColumnStyle,
    *,
    retry_ambiguous: bool = False,
    progress: ProgressCounter | None = None,
) -> list[RemoteIssue]:
    """Create approved draft files and safely replace them with real cards."""
    if not drafts:
        return []

    columns = {column.column_id: column for column in provider.columns(project.project_id)}
    folders = layout.folder_index(list(columns.values()), column_style)
    made: list[RemoteIssue] = []
    journal_path = ensure_workspace_path(workspace, layout.pending_creates_file(workspace))
    journal = PendingCreateJournal.load(journal_path)

    for entry in tracked_items(drafts_in_name_order(drafts), progress, draft_title):
        if entry.file.front.get("components") and "components" not in provider.creatable_card_fields():
            report.skipped.append((entry.path.name, f"{provider.spec.label} cannot create card components"))
            report.held.append(entry.path.name)
            continue
        draft_id = str(entry.file.front.get("id", "") or entry.path.stem)
        pending = journal.attempts.get(draft_id)
        if pending is not None and not retry_ambiguous:
            changed = pending.signature != draft_signature(entry)
            suffix = " and the draft changed afterward" if changed else ""
            report.skipped.append(
                (
                    entry.path.name,
                    f"previous create outcome is unknown{suffix}; not retried automatically",
                )
            )
            report.held.append(entry.path.name)
            continue

        front = entry.file.front
        column = folders.get(entry.column_name)
        assignee = str(front.get("assignee", "") or "")
        draft = IssueDraft(
            title=draft_title(entry),
            body=entry.file.source,
            issue_type=str(front.get("type", "") or ""),
            column_id=column.column_id if column else "",
            column_name=column.name if column else "",
            priority=str(front.get("priority", "") or ""),
            labels=tuple(str(value) for value in front.get("labels", []) or []),
            components=tuple(str(value) for value in front.get("components", []) or []),
            due_date=parse_date(front.get("due") or front.get("due_date")),
            parent_key=str(front.get("parent", "") or ""),
            assignee=assignee,
            assignee_ids=_assignee_ids(provider, assignee),
        )
        journal.begin(
            journal_path,
            draft_id,
            filename=entry.path.name,
            signature=draft_signature(entry),
        )
        try:
            issue = provider.create_issue(project.project_id, draft)
        except (TransportError, PayloadError) as error:
            report.skipped.append(
                (
                    entry.path.name,
                    "create outcome is unknown because the provider did not return "
                    f"a trustworthy confirmation: {str(error).splitlines()[0]}",
                )
            )
            report.held.append(entry.path.name)
            continue
        except (ProviderError, UnsupportedError) as error:
            journal.resolve(journal_path, draft_id)
            report.skipped.append((entry.path.name, str(error).splitlines()[0]))
            report.held.append(entry.path.name)
            continue
        journal.resolve(journal_path, draft_id)

        target_column = columns.get(issue.column_id) or column
        if target_column is not None:
            target = ensure_workspace_path(
                workspace,
                layout.issue_path(workspace, provider.spec.name, project, target_column, issue, column_style),
            )
            write_text_atomic(
                target,
                markdown.render(
                    issue,
                    column_name=layout.column_folder(target_column, column_style),
                    notes=entry.file.notes,
                    provider=provider.spec.name,
                    agent_block=entry.file.agent_block,
                    comments=tuple(
                        comment.model_copy(update={"issue_id": issue.issue_id})
                        for comment in entry.file.comments
                    ),
                    comment_drafts=tuple(
                        draft.model_copy(update={"issue_id": issue.issue_id})
                        for draft in entry.file.comment_drafts
                    ),
                    include_comment_region=entry.file.has_comment_region,
                ),
            )
            if target != entry.path:
                ensure_workspace_path(workspace, entry.path).unlink(missing_ok=True)

        state.remember(issue)
        report.created.append(issue.display_key())
        made.append(issue)

    if made:
        state.save(ensure_workspace_path(workspace, layout.state_file(workspace)))
    return made


def apply_plan(
    provider: Provider,
    plan: SyncPlan,
    report: SyncReport,
    *,
    push_conflicts: bool = False,
    accept_remote_conflicts: bool = False,
    conflict_resolutions: Mapping[str, Mapping[str, ConflictResolution]] | None = None,
    progress: ProgressCounter | None = None,
) -> set[str]:
    """Apply approved edits and return ids no longer held as local changes."""
    sent: set[str] = set()
    for pending in tracked_items(plan.pushes, progress, lambda item: item.key):
        edit = pending.edit
        unsupported = edit.unsupported(provider.editable_card_fields())
        if unsupported:
            report.skipped.append((pending.key, f"cannot write {', '.join(unsupported)}"))
            continue
        if pending.conflict:
            if conflict_resolutions is not None:
                conflict_fields = set(pending.conflicting_fields())
                choices = conflict_resolutions.get(pending.previous.issue_id, {})
                undecided = sorted(
                    field_name
                    for field_name in conflict_fields
                    if choices.get(field_name, ConflictResolution.HOLD) is ConflictResolution.HOLD
                )
                if undecided:
                    report.skipped.append(
                        (pending.key, f"conflict not decided for {', '.join(undecided)}; card held locally")
                    )
                    continue
                local_fields = {
                    field_name
                    for field_name in conflict_fields
                    if choices.get(field_name) is ConflictResolution.LOCAL
                }
                provider_fields = conflict_fields - local_fields
                selected = (set(edit.touched()) - conflict_fields) | local_fields
                if provider_fields:
                    report.accepted.append(pending.key)
                if not selected:
                    sent.add(pending.previous.issue_id)
                    continue
                edit = _subset_edit(edit, selected)
            elif accept_remote_conflicts:
                conflict_fields = set(pending.conflicting_fields())
                selected = set(edit.touched()) - conflict_fields
                report.accepted.append(pending.key)
                if selected:
                    edit = _subset_edit(edit, selected)
                else:
                    sent.add(pending.previous.issue_id)
                    continue
            elif not push_conflicts:
                report.skipped.append((pending.key, "changed on the tracker too; not overwritten"))
                continue
        if pending.unchecked and not push_conflicts:
            report.skipped.append((pending.key, "could not check for provider changes; not sent"))
            continue
        try:
            provider.update_issue(pending.remote or pending.previous, edit)
        except (ProviderError, UnsupportedError) as error:
            report.skipped.append((pending.key, str(error).splitlines()[0]))
            continue
        report.pushed.append(pending.key)
        sent.add(pending.previous.issue_id)
    return sent


def _subset_edit(edit: IssueEdit, selected: set[str]) -> IssueEdit:
    """Return only reviewed fields from an immutable neutral edit."""
    values = {
        field_name: getattr(edit, field_name)
        for field_name in selected
        if field_name not in edit.cleared
    }
    cleared = tuple(field_name for field_name in edit.cleared if field_name in selected)
    return IssueEdit(**values, cleared=cleared)


def _assignee_ids(provider: Provider, named: str) -> tuple[str, ...]:
    """Resolve a draft assignee only when it unambiguously names the user."""
    if not named.strip():
        return ()
    try:
        me = provider.verify()
    except (ProviderError, UnsupportedError):
        return ()

    handles = {
        value.strip().casefold() for value in (me.email, me.display_name, me.username, me.account_id) if value.strip()
    }
    if not me.account_id or named.strip().casefold() not in handles:
        return ()
    return (me.account_id,)
