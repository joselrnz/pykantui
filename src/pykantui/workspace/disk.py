"""Read and reconcile the local Markdown representation of a board."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pykantui import git
from pykantui.config.paths import write_text_atomic
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError, UnsupportedError
from pykantui.tracker.models import RemoteColumn, RemoteComment, RemoteIssue, RemoteProject
from pykantui.workspace import layout, markdown
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.models import SyncReport
from pykantui.workspace.paths import ensure_workspace_path
from pykantui.workspace.progress import (
    ProgressCounter,
    SyncPhase,
    SyncProgressCallback,
    emit_progress,
    tracked_items,
)

ARCHIVE_DIR = "archive"


@dataclass(frozen=True, slots=True)
class OnDisk:
    """One parsed issue file and its location in the local board."""

    path: Path
    column_name: str
    file: markdown.IssueFile


def read_disk(
    workspace: Path,
    provider_name: str,
    project: RemoteProject,
    report: SyncReport | None = None,
) -> dict[str, OnDisk]:
    """Read issue files keyed by stable provider id and report duplicates."""
    found: dict[str, OnDisk] = {}
    for path in layout.iter_issue_files(workspace, provider_name, project):
        try:
            parsed = markdown.read(ensure_workspace_path(workspace, path))
        except OSError:
            continue
        issue_id = str(parsed.front.get("id", "") or "")
        if not parsed.valid:
            issue_id = issue_id or f"invalid:{path.relative_to(workspace).as_posix()}"
            found[issue_id] = OnDisk(path=path, column_name="", file=parsed)
            if report is not None:
                report.skipped.append((path.name, f"invalid Markdown: {'; '.join(parsed.errors)}"))
            continue
        if not issue_id:
            continue
        seen = found.get(issue_id)
        if seen is not None:
            if report is not None:
                report.skipped.append((path.name, f"another file claims the same id ({seen.path.name} wins)"))
            continue
        column = layout.column_name_of(path, workspace, provider_name, project)
        found[issue_id] = OnDisk(path=path, column_name=column, file=parsed)
    return found


def ensure_column_dirs(
    workspace: Path,
    provider_name: str,
    project: RemoteProject,
    columns: list[RemoteColumn],
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
) -> None:
    """Create a safe local directory for every provider column."""
    for column in columns:
        ensure_workspace_path(
            workspace, layout.column_dir(workspace, provider_name, project, column, column_style)
        ).mkdir(parents=True, exist_ok=True)


def write_issues(
    workspace: Path,
    provider_name: str,
    project: RemoteProject,
    issues: list[RemoteIssue],
    by_id: dict[str, RemoteColumn],
    on_disk: dict[str, OnDisk],
    report: SyncReport,
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
    held_ids: set[str] | None = None,
    comments_by_issue: dict[str, tuple[RemoteComment, ...]] | None = None,
    sent_comment_ids: set[str] | None = None,
    progress: ProgressCounter | None = None,
) -> set[str]:
    """Write provider cards without overwriting local unsent edits or notes."""
    held = held_ids or set()
    comment_overrides = comments_by_issue or {}
    sent_drafts = sent_comment_ids or set()
    reconciled: set[str] = set()
    for issue in tracked_items(issues, progress, lambda item: item.display_key()):
        if issue.issue_id in held:
            continue
        column = by_id.get(issue.column_id) or RemoteColumn(
            column_id=issue.column_id, name=issue.status or "Unsorted"
        )
        target = ensure_workspace_path(
            workspace, layout.issue_path(workspace, provider_name, project, column, issue, column_style)
        )
        existing = on_disk.get(issue.issue_id)
        if existing is None and target.is_file():
            # A successful create writes its canonical file after ``on_disk``
            # was captured. Read that exact, id-matching file back so the pull
            # phase preserves local notes and pending comments instead of
            # immediately overwriting them with an empty discussion region.
            created_file = markdown.read(target)
            if created_file.valid and str(created_file.front.get("id", "")) == issue.issue_id:
                existing = OnDisk(
                    path=target,
                    column_name=layout.column_name_of(
                        target,
                        workspace,
                        provider_name,
                        project,
                    ),
                    file=created_file,
                )
        protected = next(
            (entry for entry in on_disk.values() if entry.path == target and not entry.file.valid),
            None,
        )
        if protected is not None:
            if not any(name == target.name and "invalid Markdown" in why for name, why in report.skipped):
                report.skipped.append((target.name, "invalid Markdown: local file was preserved"))
            continue
        notes = existing.file.notes if existing else ""
        agent_block = existing.file.agent_block if existing else ""
        comments = comment_overrides.get(
            issue.issue_id,
            existing.file.comments if existing else (),
        )
        comment_drafts = tuple(
            draft
            for draft in (existing.file.comment_drafts if existing else ())
            if draft.local_id not in sent_drafts
        )
        include_comment_region = bool(
            issue.issue_id in comment_overrides
            or (existing is not None and existing.file.has_comment_region)
        )

        if existing and existing.path != target:
            source = ensure_workspace_path(workspace, existing.path)
            if not git.move(workspace, source, target):
                raise ProviderError(
                    f"could not move {existing.path.name} into {column.name}",
                    hint="The local file was left in place; fix the path error and sync again.",
                )
            report.moved.append((str(existing.path.name), column.name))

        rendered = markdown.render(
            issue,
            column_name=layout.column_folder(column, column_style),
            notes=notes,
            provider=provider_name,
            comments=comments,
            comment_drafts=comment_drafts,
            include_comment_region=include_comment_region,
            agent_block=agent_block,
        )
        if existing and existing.path == target and _unchanged(workspace, target, rendered):
            reconciled.add(issue.issue_id)
            continue
        write_text_atomic(target, rendered)
        report.written.append(issue.display_key())
        reconciled.add(issue.issue_id)
    return reconciled


def prune(
    workspace: Path,
    project: RemoteProject,
    mine: list[RemoteIssue],
    everything: list[RemoteIssue],
    on_disk: dict[str, OnDisk],
    report: SyncReport,
    provider: Provider | None = None,
    progress: SyncProgressCallback | None = None,
) -> None:
    """Archive reassigned cards and remove cards confirmed absent upstream."""
    from pykantui.commands.new import is_draft  # noqa: PLC0415 - avoids a command/workspace cycle

    live = {issue.issue_id for issue in mine}
    on_tracker = {issue.issue_id for issue in everything}
    candidates = [
        (issue_id, entry)
        for issue_id, entry in sorted(on_disk.items())
        if entry.file.valid and issue_id not in live and not is_draft(issue_id)
    ]
    counter = ProgressCounter(
        progress,
        SyncPhase.VERIFYING,
        len(candidates),
        "Checking missing and reassigned cards",
    )
    if not candidates:
        emit_progress(
            progress,
            SyncPhase.VERIFYING,
            completed=0,
            total=0,
            summary=counter.summary,
        )
    for issue_id, entry in tracked_items(
        candidates,
        counter,
        lambda item: str(item[1].file.front.get("key", "") or item[1].path.stem),
    ):
        if issue_id in on_tracker:
            _archive(workspace, entry, report)
            continue
        if provider is not None and _still_there(provider, project, issue_id, entry):
            continue
        try:
            ensure_workspace_path(workspace, entry.path).unlink()
        except OSError:
            continue
        report.deleted.append(entry.path.name)


def write_board_file(
    workspace: Path,
    provider_name: str,
    project: RemoteProject,
    columns: list[RemoteColumn],
    issues: list[RemoteIssue],
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
) -> None:
    """Regenerate the readable Markdown index for a local board."""
    by_column: dict[str, list[RemoteIssue]] = {}
    for issue in issues:
        by_column.setdefault(issue.column_id, []).append(issue)

    lines = [f"# {project.label()}", "", f"{len(issues)} issues · synced from {provider_name}", ""]
    for column in columns:
        held = by_column.get(column.column_id, [])
        lines.extend((f"## {column.name} ({len(held)})", ""))
        for issue in held:
            href = layout.link_to(layout.column_folder(column, column_style), issue.filename())
            lines.append(f"- [{issue.display_key()}]({href}) {issue.title}")
        if not held:
            lines.append("_empty_")
        lines.append("")

    target = ensure_workspace_path(
        workspace, layout.project_dir(workspace, provider_name, project) / layout.BOARD_FILE
    )
    write_text_atomic(target, "\n".join(lines).rstrip() + "\n")


def _unchanged(workspace: Path, path: Path, rendered: str) -> bool:
    try:
        return ensure_workspace_path(workspace, path).read_text(encoding="utf-8") == rendered
    except OSError:
        return False


def _archive(workspace: Path, entry: OnDisk, report: SyncReport) -> None:
    source = ensure_workspace_path(workspace, entry.path)
    target = ensure_workspace_path(workspace, workspace / ARCHIVE_DIR / entry.path.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(target)
    except OSError:
        return
    report.archived.append(entry.path.name)


def _still_there(provider: Provider, project: RemoteProject, issue_id: str, entry: OnDisk) -> bool:
    probe = RemoteIssue(
        issue_id=issue_id,
        key=str(entry.file.front.get("key", "") or ""),
        title=str(entry.file.front.get("title", "") or ""),
    )
    try:
        return provider.get_issue(project.project_id, probe) is not None
    except (ProviderError, UnsupportedError):
        return True
