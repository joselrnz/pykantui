"""Issue-scoped comment reconciliation for workspace sync."""

from __future__ import annotations

from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject
from pykantui.workspace.disk import OnDisk
from pykantui.workspace.models import SyncReport
from pykantui.workspace.outbound import CommentApplyResult
from pykantui.workspace.progress import (
    ProgressCounter,
    SyncPhase,
    SyncProgressCallback,
    emit_progress,
    tracked_items,
)


def comments_for_sync(
    provider: Provider,
    project: RemoteProject,
    issues: list[RemoteIssue],
    on_disk: dict[str, OnDisk],
    report: SyncReport,
    explicitly_requested: set[str],
    applied: CommentApplyResult | None,
    progress: SyncProgressCallback | None = None,
) -> tuple[dict[str, tuple[RemoteComment, ...]], set[str]]:
    """Fetch only opted-in discussions and reconcile confirmed POSTs.

    Comments are an issue-scoped endpoint on every supported provider. Reading
    them for every card would turn one board sync into an N+1 request storm, so
    a card opts in by carrying a comment region or draft, or by an explicit
    request from the Comments tab.
    """
    targets = set(explicitly_requested)
    if not explicitly_requested:
        targets.update(
            issue_id
            for issue_id, entry in on_disk.items()
            if entry.file.has_comment_region or entry.file.comment_drafts
        )
    if applied is not None:
        targets.update(applied.posted)
        targets.update(applied.draft_issue_ids.values())

    found: dict[str, tuple[RemoteComment, ...]] = {}
    by_id = {issue.issue_id: issue for issue in issues}
    target_issues = [by_id[issue_id] for issue_id in sorted(targets) if issue_id in by_id]
    counter = ProgressCounter(
        progress,
        SyncPhase.COMMENTS,
        len(target_issues),
        "Downloading opted-in comment threads",
    )
    if not target_issues:
        emit_progress(
            progress,
            SyncPhase.COMMENTS,
            completed=0,
            total=0,
            summary=counter.summary,
        )
    for issue in tracked_items(target_issues, counter, lambda item: item.display_key()):
        issue_id = issue.issue_id
        existing = on_disk.get(issue_id)
        comments = existing.file.comments if existing is not None else ()
        if provider.spec.capabilities.read_comments:
            try:
                comments = provider.comments(
                    project.project_id,
                    issue,
                    refresh=issue_id in explicitly_requested,
                )
            except ProviderError as error:
                report.skipped.append(
                    (issue.display_key(), f"comments: {str(error).splitlines()[0]}")
                )
        found[issue_id] = _merge_comments(
            comments,
            tuple(applied.posted.get(issue_id, ())) if applied is not None else (),
        )

    return found, _finalized_draft_ids(applied, found) if applied is not None else set()


def _finalized_draft_ids(
    applied: CommentApplyResult,
    found: dict[str, tuple[RemoteComment, ...]],
) -> set[str]:
    """Confirm a local draft only against the same provider issue thread."""

    posted_ids = {
        issue_id: {comment.comment_id for comment in comments}
        for issue_id, comments in applied.posted.items()
    }
    available_ids = {
        issue_id: {comment.comment_id for comment in comments}
        for issue_id, comments in found.items()
    }
    finalized: set[str] = set()
    for local_id, remote_id in applied.confirmed_remote_ids.items():
        issue_id = applied.draft_issue_ids.get(local_id, "")
        if remote_id in posted_ids.get(issue_id, set()) or remote_id in available_ids.get(issue_id, set()):
            finalized.add(local_id)
    return finalized


def _merge_comments(
    first: tuple[RemoteComment, ...],
    second: tuple[RemoteComment, ...],
) -> tuple[RemoteComment, ...]:
    """Merge provider and just-posted results without duplicating an id."""
    ordered: dict[str, RemoteComment] = {}
    for comment in (*first, *second):
        ordered[comment.comment_id] = comment
    return tuple(ordered.values())


__all__ = ["comments_for_sync"]
