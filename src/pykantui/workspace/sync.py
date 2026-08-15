"""Coordinate explicit, confirmation-gated provider and Markdown syncs.

The order is deliberate: inspect local edits, checkpoint them, push approved
changes, bypass the cache for a provider-fresh pull, write Markdown, then make
the local after-sync checkpoint. Planning, outbound translation, and disk
reconciliation live in focused modules so this file only owns the workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.mine import Identity, Scope, owns
from pykantui.tracker.models import RemoteIssue, RemoteProject
from pykantui.workspace import layout
from pykantui.workspace import progress as sync_progress
from pykantui.workspace.cache import workspace_cache
from pykantui.workspace.checkpoints import (
    checkpoint_after_sync,
    checkpoint_before_provider_write,
    require_local_git_when_versioned,
)
from pykantui.workspace.comments import comments_for_sync
from pykantui.workspace.disk import (
    ARCHIVE_DIR,
    ensure_column_dirs,
    prune,
    read_disk,
    write_board_file,
    write_issues,
)
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.locking import with_workspace_lock
from pykantui.workspace.models import ConfirmPush, ConflictResolution, PendingPush, SyncPlan, SyncReport
from pykantui.workspace.outbound import (
    CommentApplyResult,
    apply_comment_plan,
    apply_plan,
    create_drafts,
    draft_preview,
    draft_signature,
    draft_title,
    drafts_in_name_order,
    pending_drafts,
)
from pykantui.workspace.paths import ensure_workspace_path
from pykantui.workspace.planner import build_plan
from pykantui.workspace.state import SyncState, update_after_sync

__all__ = [
    "ARCHIVE_DIR",
    "ConfirmPush",
    "PendingPush",
    "SyncPlan",
    "SyncReport",
    "build_plan",
    "preview",
    "sync",
]


def preview(
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    *,
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
) -> SyncPlan:
    """Describe outbound local work without writing files or the provider."""
    state = SyncState.load(ensure_workspace_path(workspace, layout.state_file(workspace)))
    on_disk = read_disk(workspace, provider.spec.name, project)
    plan = build_plan(provider, project, on_disk, state, column_style, check_remote=True)
    report = SyncReport()
    drafts = pending_drafts(provider, on_disk, report)
    ordered = drafts_in_name_order(drafts)
    plan.creates = [draft_title(entry) for entry in ordered]
    plan.create_details = [draft_signature(entry) for entry in ordered]
    creatable = provider.creatable_card_fields()
    plan.create_previews = [draft_preview(entry, creatable) for entry in ordered]
    return plan


@sync_progress.report_sync_progress
@with_workspace_lock
def sync(
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    *,
    push_edits: bool = True,
    commit: bool = True,
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
    confirm: ConfirmPush | None = None,
    push_conflicts: bool = False,
    accept_remote_conflicts: bool = False,
    conflict_resolutions: Mapping[str, Mapping[str, ConflictResolution]] | None = None,
    known_conflicts: set[str] | None = None,
    identity: Identity | None = None,
    scope: Scope | None = None,
    retry_ambiguous_creates: bool = False,
    retry_ambiguous_comments: bool = False,
    refresh_comments_for: set[str] | None = None,
    progress: sync_progress.SyncProgressCallback | None = None,
) -> SyncReport:
    """Reconcile one project between its provider and local Markdown.

    Interactive callers pass ``confirm`` to guard every outbound batch. A
    declined push still performs the pull while preserving unsent local files.
    ``commit`` controls local Git checkpoints only; this workflow never pushes
    to, pulls from, or otherwise configures a Git remote.
    """
    report = SyncReport()
    if push_conflicts and accept_remote_conflicts:
        raise ProviderError("cannot overwrite and accept provider conflicts in the same sync")
    provider_name = provider.spec.name
    require_local_git_when_versioned(workspace, commit)

    cache = workspace_cache(workspace, provider_name, project, credentials=provider.secrets)
    provider.use_cache(cache)
    report.cache = cache

    state_path = ensure_workspace_path(workspace, layout.state_file(workspace))
    state = SyncState.load(state_path)
    on_disk = read_disk(workspace, provider_name, project, report)
    plan = build_plan(provider, project, on_disk, state, column_style, check_remote=push_edits)
    report.plan = plan

    drafts = pending_drafts(provider, on_disk, report) if push_edits else {}
    ordered_drafts = drafts_in_name_order(drafts)
    plan.creates = [draft_title(entry) for entry in ordered_drafts]
    plan.create_details = [draft_signature(entry) for entry in ordered_drafts]
    creatable = provider.creatable_card_fields()
    plan.create_previews = [draft_preview(entry, creatable) for entry in ordered_drafts]

    versioned = commit and (workspace / ".git").exists()
    checkpoint_before_provider_write(workspace, provider_name, project, versioned)

    pending_ids = {item.previous.issue_id for item in plan.pushes}
    pending_ids.update(item.issue_id for item in plan.invalid if state.get(item.issue_id) is not None)
    sent_ids: set[str] = set()
    created: list[RemoteIssue] = []
    comment_result: CommentApplyResult | None = None

    if push_edits:
        approved = plan.is_empty() or confirm is None or bool(confirm(plan))
        report.declined = not approved
        if approved:
            applying = sync_progress.ProgressCounter(
                progress,
                sync_progress.SyncPhase.APPLYING,
                len(ordered_drafts) + len(plan.pushes) + len(plan.comment_pushes),
                "Processing approved provider changes",
            )
            created = create_drafts(
                workspace,
                provider,
                project,
                drafts,
                state,
                report,
                column_style,
                retry_ambiguous=retry_ambiguous_creates,
                progress=applying,
            )
            sent_ids = apply_plan(
                provider,
                plan,
                report,
                push_conflicts=push_conflicts,
                accept_remote_conflicts=accept_remote_conflicts,
                conflict_resolutions=conflict_resolutions,
                progress=applying,
            )
            comment_result = apply_comment_plan(
                workspace,
                provider,
                project,
                plan,
                on_disk,
                report,
                retry_ambiguous=retry_ambiguous_comments,
                progress=applying,
            )
        else:
            report.held.extend(entry.path.name for entry in ordered_drafts)

    held_ids = pending_ids - sent_ids
    report.held = sorted(
        {*report.held} | {on_disk[issue_id].path.name for issue_id in held_ids if issue_id in on_disk}
    )

    provider.refresh()
    columns = provider.columns(project.project_id)
    by_id = {column.column_id: column for column in columns}
    everything = sync_progress.collect_items(
        provider.iter_issues(project.project_id),
        progress,
        sync_progress.SyncPhase.FETCHING,
        lambda issue: issue.display_key(),
        "Downloading provider cards",
    )
    issues = _mine_only(everything, identity, scope)
    report.considered = len(everything)
    report.mine = len(issues)

    ensure_column_dirs(workspace, provider_name, project, columns, column_style)
    comments_by_issue, finalized_comment_ids = comments_for_sync(
        provider,
        project,
        issues,
        on_disk,
        report,
        refresh_comments_for or set(),
        comment_result,
        progress,
    )
    reconciling = sync_progress.ProgressCounter(
        progress,
        sync_progress.SyncPhase.RECONCILING,
        len(issues),
        "Writing provider cards to Markdown",
        announce_each=False,
    )
    if not issues:
        sync_progress.emit_progress(
            progress,
            sync_progress.SyncPhase.RECONCILING,
            completed=0,
            total=0,
            summary=reconciling.summary,
        )
    reconciled = write_issues(
        workspace,
        provider_name,
        project,
        issues,
        by_id,
        on_disk,
        report,
        column_style,
        held_ids,
        comments_by_issue=comments_by_issue,
        sent_comment_ids=finalized_comment_ids,
        progress=reconciling,
    )
    if comment_result is not None:
        for local_id in sorted(finalized_comment_ids):
            issue_id = comment_result.draft_issue_ids.get(local_id, "")
            if issue_id in reconciled:
                comment_result.journal.resolve(comment_result.journal_path, local_id)
    prune(
        workspace,
        project,
        issues,
        everything,
        on_disk,
        report,
        provider,
        progress,
    )
    write_board_file(workspace, provider_name, project, columns, issues, column_style)

    sync_progress.emit_progress(
        progress, sync_progress.SyncPhase.FINALIZING,
        completed=reconciling.completed, total=reconciling.total, item=reconciling.item,
        summary="Saving sync state and local history",
    )
    update_after_sync(state, issues, created, held_ids, plan, push_edits, known_conflicts)
    state.save(state_path)
    checkpoint_after_sync(workspace, provider_name, project, report, versioned)
    return report


def _mine_only(
    issues: list[RemoteIssue],
    identity: Identity | None,
    scope: Scope | None,
) -> list[RemoteIssue]:
    if identity is None or (scope is not None and scope.describes_all()):
        return issues
    return [issue for issue in issues if owns(issue, identity, scope)]
