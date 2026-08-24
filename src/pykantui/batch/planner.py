"""Provider-aware, read-only compilation of a batch manifest."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pykantui.batch.models import BatchIssue, BatchManifest, BatchState, FieldSource, manifest_sha256
from pykantui.config.paths import write_text_atomic
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_TODO,
    IssueType,
    RemoteColumn,
    RemoteProject,
    slugify,
)

PLAN_SCHEMA = 1
PLAN_EXPIRY_MINUTES = 10


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class PlannedTransition(PlanModel):
    column_id: str
    name: str


class BatchOperation(PlanModel):
    ref: str
    title: str
    body: str = ""
    issue_type: str = ""
    issue_type_id: str = ""
    parent_ref: str = ""
    priority: str = ""
    labels: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    due_date: str = ""
    assignee: str = ""
    initial_column_id: str = ""
    initial_column_name: str = ""
    transitions: tuple[PlannedTransition, ...] = ()
    sources: dict[str, FieldSource] = Field(default_factory=dict)

    def signature(self) -> str:
        material = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class BatchPlan(PlanModel):
    schema_version: Literal[1] = Field(default=1, alias="schema")
    batch_id: str
    provider: str
    project_id: str
    source_path: str
    source_hash: str
    created_at: datetime
    expires_at: datetime
    operations: tuple[BatchOperation, ...]
    warnings: tuple[str, ...] = ()
    plan_hash: str = ""

    def calculated_hash(self) -> str:
        material = json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude={"plan_hash"}),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def verify_hash(self) -> bool:
        return bool(self.plan_hash) and self.plan_hash == self.calculated_hash()

    def with_hash(self) -> BatchPlan:
        return self.model_copy(update={"plan_hash": self.calculated_hash()})

    def describe(self) -> str:
        lines = [f"READY TO CREATE ({len(self.operations)})"]
        for operation in self.operations:
            parent = f" under {operation.parent_ref}" if operation.parent_ref else ""
            route = " → ".join(item.name for item in operation.transitions)
            destination = f" [{route}]" if route else ""
            ai = sorted(name for name, source in operation.sources.items() if source is FieldSource.AI)
            inferred = f" (AI: {', '.join(ai)})" if ai else ""
            lines.append(f"  + {operation.ref}: {operation.title}{parent}{destination}{inferred}")
        if self.warnings:
            lines += ["", "WARNINGS", *(f"  ! {warning}" for warning in self.warnings)]
        lines += ["", f"Plan: {self.plan_hash}", f"Expires: {self.expires_at.isoformat()}"]
        return "\n".join(lines)


def build_batch_plan(
    manifest: BatchManifest,
    source_path: Path,
    provider: Provider,
    project: RemoteProject,
) -> BatchPlan:
    """Resolve types and states without making a provider write."""
    if manifest.target.provider != provider.spec.name:
        raise ProviderError(
            f"manifest targets {manifest.target.provider}, but this workspace uses {provider.spec.name}"
        )
    wanted_project = manifest.target.project.strip()
    if wanted_project and wanted_project.casefold() not in {
        project.project_id.casefold(),
        project.key.casefold(),
        project.name.casefold(),
    }:
        raise ProviderError(f"manifest project {wanted_project!r} does not match {project.label()}")
    if not provider.spec.capabilities.create_issues:
        raise ProviderError(f"{provider.spec.label} cannot create issues")

    columns = provider.columns(project.project_id)
    if not columns:
        raise ProviderError(f"{provider.spec.label} project has no states")
    default_column = _default_column(columns)
    operations: list[BatchOperation] = []

    for issue in manifest.ordered_issues():
        if not issue.title:
            raise ProviderError(f"{issue.ref} needs a title before it can be planned")
        requested_type = issue.issue_type or manifest.defaults.issue_type
        issue_type = provider.resolve_issue_type(project.project_id, requested_type)
        _validate_hierarchy(provider, issue, issue_type)
        state = issue.state or manifest.defaults.state
        transitions = _resolve_transitions(columns, state, default_column)
        labels = tuple(dict.fromkeys((*manifest.defaults.labels, *issue.labels)))
        components = tuple(dict.fromkeys((*manifest.defaults.components, *issue.components)))
        priority = issue.priority or manifest.defaults.priority
        _validate_fields(provider, issue, requested_type, labels, components, priority)
        operations.append(
            BatchOperation(
                ref=issue.ref,
                title=issue.title,
                body=issue.body or "",
                issue_type=issue_type.name if issue_type else requested_type,
                issue_type_id=issue_type.type_id if issue_type else "",
                parent_ref=issue.parent_ref,
                priority=priority,
                labels=labels,
                components=components,
                due_date=issue.due_date.isoformat() if issue.due_date else "",
                assignee=issue.assignee,
                initial_column_id=default_column.column_id,
                initial_column_name=default_column.name,
                transitions=transitions,
                sources=issue.sources,
            )
        )

    now = datetime.now(UTC)
    return BatchPlan(
        batch_id=manifest.metadata.name,
        provider=provider.spec.name,
        project_id=project.project_id,
        source_path=str(source_path.expanduser().resolve()),
        source_hash=manifest_sha256(source_path),
        created_at=now,
        expires_at=now + timedelta(minutes=PLAN_EXPIRY_MINUTES),
        operations=tuple(operations),
    ).with_hash()


def write_batch_plan(path: Path, plan: BatchPlan, *, force: bool = False) -> None:
    target = path.expanduser().resolve()
    if target.exists() and not force:
        raise ProviderError(f"{target} already exists", hint="Pass --force to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, plan.model_dump_json(indent=2, by_alias=True))


def load_batch_plan(path: Path) -> BatchPlan:
    target = path.expanduser().resolve()
    try:
        if target.stat().st_size > 2_097_152:
            raise ProviderError("batch plan file is too large")
        plan = BatchPlan.model_validate_json(target.read_text(encoding="utf-8"))
    except ProviderError:
        raise
    except FileNotFoundError as error:
        raise ProviderError(f"batch plan does not exist: {target}") from error
    except (OSError, UnicodeError, ValidationError, ValueError) as error:
        raise ProviderError(f"invalid batch plan: {str(error).splitlines()[0]}") from error
    if not plan.verify_hash():
        raise ProviderError("batch plan was changed or tampered with; run plan again")
    return plan


def _validate_hierarchy(provider: Provider, issue: BatchIssue, issue_type: IssueType | None) -> None:
    if issue.parent_ref and not provider.spec.capabilities.parent_issues:
        raise ProviderError(f"{provider.spec.label} does not support parent issues in declarative batches")
    if issue_type is None:
        return
    if issue_type.subtask and not issue.parent_ref:
        raise ProviderError(f"{issue.ref}: issue type {issue_type.name!r} requires parent")
    if issue.parent_ref and not issue_type.subtask:
        raise ProviderError(f"{issue.ref}: issue type {issue_type.name!r} is not a sub-task")


def _validate_fields(
    provider: Provider,
    issue: BatchIssue,
    requested_type: str,
    labels: tuple[str, ...],
    components: tuple[str, ...],
    priority: str,
) -> None:
    allowed = set(provider.creatable_card_fields())
    requested = {"title", "column_id"}
    if issue.body:
        requested.add("body")
    if requested_type:
        requested.add("issue_type")
    if labels:
        requested.add("labels")
    if components:
        requested.add("components")
    if priority:
        requested.add("priority")
    if issue.due_date:
        requested.add("due_date")
    if issue.assignee:
        requested.add("assignee")
    unsupported = sorted(requested - allowed)
    if unsupported:
        raise ProviderError(f"{provider.spec.label} cannot create fields: {', '.join(unsupported)}")


def _resolve_transitions(
    columns: list[RemoteColumn],
    state: BatchState | None,
    default: RemoteColumn,
) -> tuple[PlannedTransition, ...]:
    if state is None:
        return ()
    route = list(state.via)
    if not route or route[-1].casefold() != state.name.casefold():
        route.append(state.name)
    resolved = [_pick_column(columns, name) for name in route]
    compact: list[RemoteColumn] = []
    previous = ""
    for column in resolved:
        if column.column_id != previous:
            compact.append(column)
            previous = column.column_id
    return tuple(PlannedTransition(column_id=column.column_id, name=column.name) for column in compact)


def _default_column(columns: list[RemoteColumn]) -> RemoteColumn:
    for group in (COLUMN_TODO, COLUMN_BACKLOG):
        found = next((column for column in columns if column.group == group), None)
        if found is not None:
            return found
    return columns[0]


def _pick_column(columns: list[RemoteColumn], wanted: str) -> RemoteColumn:
    needle = wanted.strip().casefold()
    matches = [
        column
        for column in columns
        if needle in {column.column_id.casefold(), column.name.casefold(), slugify(column.name).casefold()}
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ProviderError(
            f"no state matching {wanted!r}",
            hint="Available states: " + ", ".join(column.name for column in columns),
        )
    raise ProviderError(f"state {wanted!r} is ambiguous")


__all__ = [
    "BatchOperation",
    "BatchPlan",
    "PlannedTransition",
    "build_batch_plan",
    "load_batch_plan",
    "write_batch_plan",
]
