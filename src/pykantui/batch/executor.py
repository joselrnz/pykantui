"""Apply one reviewed batch plan with durable per-item recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from pykantui.api.errors import PayloadError, TransportError
from pykantui.batch.journal import BatchApplyJournal, BatchApplyPhase
from pykantui.batch.models import manifest_sha256
from pykantui.batch.planner import BatchOperation, BatchPlan
from pykantui.config.paths import write_text_atomic
from pykantui.core.naming import safe_name
from pykantui.mcp import tokens
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError, UnsupportedError
from pykantui.tracker.models import IssueDraft, RemoteColumn, RemoteIssue, RemoteProject
from pykantui.workspace import layout, markdown
from pykantui.workspace.paths import ensure_workspace_path
from pykantui.workspace.state import SyncState


@dataclass(slots=True)
class BatchApplyReport:
    created: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append(f"created {len(self.created)}")
        if self.completed:
            parts.append(f"completed {len(self.completed)}")
        if self.skipped:
            parts.append(f"already complete {len(self.skipped)}")
        return ", ".join(parts) or "nothing to apply"


def apply_batch_plan(
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    plan: BatchPlan,
) -> BatchApplyReport:
    """Apply exactly ``plan``; never infer or regenerate operations here."""
    root = workspace.expanduser().resolve()
    _validate_plan(root, provider, project, plan)
    journal_path = _journal_path(root, plan.batch_id)
    journal = BatchApplyJournal.load(journal_path, batch_id=plan.batch_id, plan_hash=plan.plan_hash)
    columns = provider.columns(project.project_id)
    by_id = {column.column_id: column for column in columns}
    state_path = ensure_workspace_path(root, layout.state_file(root))
    state = SyncState.load(state_path)
    report = BatchApplyReport()
    tokens.invalidate(root)

    for operation in plan.operations:
        record = journal.items.get(operation.ref)
        if record is not None and record.signature != operation.signature():
            raise ProviderError(f"{operation.ref}: batch state does not match the reviewed operation")
        draft_path = _draft_path(root, provider, project, operation, by_id)
        if record is not None and record.phase is BatchApplyPhase.COMPLETE:
            issue = record.remote_issue
            if issue is None:  # Protected by journal validation; kept defensive at the write boundary.
                raise ProviderError(f"{operation.ref}: completed batch item lost its remote issue")
            _write_confirmed_issue(root, provider, project, plan, operation, issue, draft_path, by_id)
            state.remember(issue)
            state.save(state_path)
            report.skipped.append(operation.ref)
            continue
        if record is not None and record.phase is BatchApplyPhase.CREATING:
            raise ProviderError(
                f"{operation.ref}: previous create outcome is unknown; not retried automatically",
                hint="Check the provider for the issue before retrying this batch.",
            )

        parent_key = _parent_key(operation, journal)
        if record is None:
            _write_local_draft(draft_path, provider, plan, operation, parent_key)
            journal.begin_create(journal_path, operation.ref, signature=operation.signature())
            try:
                issue = provider.create_issue(
                    project.project_id,
                    _issue_draft(operation, parent_key),
                )
            except (TransportError, PayloadError) as error:
                raise ProviderError(
                    f"{operation.ref}: create outcome is unknown; not safe to retry automatically"
                ) from error
            except (ProviderError, UnsupportedError):
                # Conservative by design. A provider may reject only after the
                # request reached it, so an unconfirmed create remains blocked.
                raise
            if not (issue.issue_id.strip() or issue.key.strip()):
                raise ProviderError(f"{operation.ref}: provider returned no issue identity")
            journal.confirm_create(journal_path, operation.ref, issue)
            report.created.append(issue.display_key())
            record = journal.items[operation.ref]

        issue = record.remote_issue
        if issue is None:
            raise ProviderError(f"{operation.ref}: batch state lost the confirmed remote issue")

        if record.phase is BatchApplyPhase.TRANSITIONING:
            issue = _recover_transition(provider, project, operation, journal, journal_path)
            record = journal.items[operation.ref]

        for hop in range(record.next_transition, len(operation.transitions)):
            transition = operation.transitions[hop]
            column = by_id.get(transition.column_id)
            if column is None or column.name != transition.name:
                raise ProviderError(f"{operation.ref}: provider state changed since planning")
            journal.begin_transition(
                journal_path,
                operation.ref,
                hop=hop,
                column_id=column.column_id,
            )
            if issue.column_id != column.column_id:
                provider.move_issue(issue, column)
            refreshed = provider.get_issue(project.project_id, issue)
            if refreshed is None or refreshed.column_id != column.column_id:
                raise ProviderError(f"{operation.ref}: provider did not confirm transition to {column.name}")
            issue = refreshed
            journal.confirm_transition(
                journal_path,
                operation.ref,
                hop=hop,
                issue=issue,
                complete=False,
            )

        _write_confirmed_issue(root, provider, project, plan, operation, issue, draft_path, by_id)
        state.remember(issue)
        state.save(state_path)
        journal.complete(journal_path, operation.ref)
        report.completed.append(operation.ref)

    return report


def _validate_plan(workspace: Path, provider: Provider, project: RemoteProject, plan: BatchPlan) -> None:
    if not plan.verify_hash():
        raise ProviderError("batch plan was changed or tampered with; run plan again")
    if plan.provider != provider.spec.name or plan.project_id != project.project_id:
        raise ProviderError("batch plan does not belong to this workspace provider/project")
    source = Path(plan.source_path)
    try:
        current_hash = manifest_sha256(source)
    except OSError as error:
        raise ProviderError("batch source manifest cannot be read") from error
    if current_hash != plan.source_hash:
        raise ProviderError("batch source changed since planning; run plan again")
    if datetime.now(UTC) > plan.expires_at and not _journal_path(workspace, plan.batch_id).exists():
        raise ProviderError("batch plan expired; run plan again")
    if any(operation.transitions for operation in plan.operations) and not provider.spec.capabilities.move_issues:
        raise ProviderError(f"{provider.spec.label} cannot move issues to requested states")
    for operation in plan.operations:
        resolved = provider.resolve_issue_type(project.project_id, operation.issue_type)
        resolved_id = resolved.type_id if resolved else ""
        if resolved_id != operation.issue_type_id:
            raise ProviderError(f"{operation.ref}: provider issue types changed since planning")


def _journal_path(workspace: Path, batch_id: str) -> Path:
    return ensure_workspace_path(
        workspace,
        layout.meta_dir(workspace) / "batches" / f"{safe_name(batch_id)}.json",
    )


def _parent_key(operation: BatchOperation, journal: BatchApplyJournal) -> str:
    if not operation.parent_ref:
        return ""
    parent = journal.items.get(operation.parent_ref)
    if parent is None or parent.remote_issue is None or parent.phase is not BatchApplyPhase.COMPLETE:
        raise ProviderError(f"{operation.ref}: parent {operation.parent_ref} did not complete")
    return parent.remote_issue.display_key()


def _issue_draft(operation: BatchOperation, parent_key: str) -> IssueDraft:
    due = date.fromisoformat(operation.due_date) if operation.due_date else None
    return IssueDraft(
        title=operation.title,
        body=operation.body,
        issue_type=operation.issue_type,
        # Create in the provider's natural/default state. Requested movement
        # is journaled and applied one explicit hop at a time afterward.
        column_id="",
        column_name=operation.initial_column_name,
        priority=operation.priority,
        labels=operation.labels,
        components=operation.components,
        due_date=due,
        parent_key=parent_key,
        assignee=operation.assignee,
    )


def _draft_path(
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    operation: BatchOperation,
    columns: dict[str, RemoteColumn],
) -> Path:
    column = columns.get(operation.initial_column_id)
    if column is None:
        raise ProviderError(f"{operation.ref}: initial provider state no longer exists")
    folder = ensure_workspace_path(workspace, layout.column_dir(workspace, provider.spec.name, project, column))
    return ensure_workspace_path(
        workspace,
        folder / f"draft-batch-{safe_name(operation.ref)}.md",
    )


def _write_local_draft(
    path: Path,
    provider: Provider,
    plan: BatchPlan,
    operation: BatchOperation,
    parent_key: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    issue = RemoteIssue(
        issue_id=path.stem,
        title=operation.title,
        body=operation.body,
        issue_type=operation.issue_type,
        column_id=operation.initial_column_id,
        status=operation.initial_column_name,
        priority=operation.priority,
        labels=operation.labels,
        components=operation.components,
        due_date=date.fromisoformat(operation.due_date) if operation.due_date else None,
        parent_key=parent_key,
        created_at=datetime.now(),
    )
    block = markdown.format_agent_block(batch_id=plan.batch_id, batch_ref=operation.ref)
    write_text_atomic(
        path,
        markdown.render(
            issue,
            column_name=path.parent.name,
            provider=provider.spec.name,
            agent_block=block,
        ),
    )


def _recover_transition(
    provider: Provider,
    project: RemoteProject,
    operation: BatchOperation,
    journal: BatchApplyJournal,
    journal_path: Path,
) -> RemoteIssue:
    record = journal.items[operation.ref]
    issue = record.remote_issue
    if issue is None:
        raise ProviderError(f"{operation.ref}: transition journal lost the remote issue")
    current = provider.get_issue(project.project_id, issue)
    if current is None:
        raise ProviderError(f"{operation.ref}: cannot verify the previous transition outcome")
    if current.column_id != record.transition_column_id:
        raise ProviderError(
            f"{operation.ref}: previous transition outcome is unknown; not retried automatically"
        )
    journal.confirm_transition(
        journal_path,
        operation.ref,
        hop=record.next_transition,
        issue=current,
        complete=False,
    )
    return current


def _write_confirmed_issue(
    workspace: Path,
    provider: Provider,
    project: RemoteProject,
    plan: BatchPlan,
    operation: BatchOperation,
    issue: RemoteIssue,
    draft_path: Path,
    columns: dict[str, RemoteColumn],
) -> None:
    column = columns.get(issue.column_id)
    if column is None:
        raise ProviderError(f"{operation.ref}: confirmed provider state is not on this board")
    target = ensure_workspace_path(
        workspace,
        layout.issue_path(workspace, provider.spec.name, project, column, issue),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    block = markdown.format_agent_block(batch_id=plan.batch_id, batch_ref=operation.ref)
    write_text_atomic(
        target,
        markdown.render(
            issue,
            column_name=layout.column_folder(column),
            provider=provider.spec.name,
            agent_block=block,
        ),
    )
    if target != draft_path:
        draft_path.unlink(missing_ok=True)


__all__ = ["BatchApplyReport", "apply_batch_plan"]
