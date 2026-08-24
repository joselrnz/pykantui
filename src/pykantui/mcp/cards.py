"""MCP mutation tools: create, move, and edit cards; assign agents; set
cross-card dependencies.

Every function here is a local mutation -- it writes Markdown, never a
provider -- and commits it for an audit trail (checkpoints elsewhere in
pykantui only wrap whole ``sync()`` calls; nothing else commits a draft
create or a card move today). The one boundary that reaches a real tracker,
``preview_sync``/``confirm_sync``, lives in ``server.py`` alongside
``tokens.py`` -- deliberately not here, since every function in this module
is meant to be safe to call without a human in the loop.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from pathlib import Path

from typing_extensions import TypedDict

from pykantui import git
from pykantui.commands.new import write_draft
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_TODO,
    IssueDraft,
    RemoteColumn,
    RemoteIssue,
    slugify,
)
from pykantui.workspace import disk, layout, markdown
from pykantui.workspace.cache import workspace_cache
from pykantui.workspace.disk import OnDisk
from pykantui.workspace.locking import exclusive_workspace
from pykantui.workspace.paths import ensure_workspace_path
from pykantui.workspace.project import Project

from . import dependencies, tokens


class CardSummary(TypedDict):
    """What every mutation tool -- and the read tools in ``server.py`` --
    return for one card, so a caller sees the same shape everywhere."""

    id: str
    key: str
    title: str
    column: str
    labels: list[str]
    blocked_by: list[str]
    assigned_agent: str
    committed: bool
    warning: str


class CardDetail(CardSummary):
    """The complete local card representation returned by ``get_card``."""

    body: str
    issue_type: str
    status: str
    priority: str
    assignee: str
    reporter: str
    components: list[str]
    created: str
    updated: str
    due: str
    parent: str
    url: str


class _Workspace:
    """One opened workspace: enough to read, draft, move, or edit a card."""

    __slots__ = ("path", "project", "provider", "columns", "folders", "on_disk")

    def __init__(
        self,
        path: Path,
        project: Project,
        provider: Provider,
        columns: list[RemoteColumn],
        folders: dict[str, RemoteColumn],
        on_disk: dict[str, OnDisk],
    ) -> None:
        self.path = path
        self.project = project
        self.provider = provider
        self.columns = columns
        self.folders = folders
        self.on_disk = on_disk


def resolve_workspace(workspace: str) -> Path:
    return Path(workspace).expanduser().resolve()


@contextlib.contextmanager
def open_workspace(workspace: str) -> Iterator[_Workspace]:
    """Load a workspace, its columns, and its current cards -- read-only.

    Not locked: reads never need exclusivity. Every mutation function below
    wraps this in :func:`pykantui.workspace.locking.exclusive_workspace`
    itself, matching how ``sync()`` holds the same lock for its own duration.
    """
    path = resolve_workspace(workspace)
    project = Project.load(path)
    with project.open() as provider:
        cache = workspace_cache(path, project.provider, project.remote(), credentials=provider.secrets)
        provider.use_cache(cache)
        columns = provider.columns(project.project_id)
        folders = layout.folder_index(columns, project.column_style)
        on_disk = disk.read_disk(path, project.provider, project.remote())
        yield _Workspace(path, project, provider, columns, folders, on_disk)


def summarize_card(entry: OnDisk) -> CardSummary:
    """The one card-summary shape every MCP tool returns."""
    front = entry.file.front
    labels = front.get("labels")
    attributes = markdown.parse_agent_block(entry.file.agent_block)
    return CardSummary(
        id=str(front.get("id", "") or ""),
        key=str(front.get("key", "") or ""),
        title=str(front.get("title", "") or ""),
        column=entry.column_name,
        labels=[str(item) for item in labels] if isinstance(labels, (list, tuple)) else [],
        blocked_by=[item.strip() for item in attributes.get("blocked-by", "").split(",") if item.strip()],
        assigned_agent=attributes.get("assigned-agent", ""),
        committed=True,
        warning="",
    )


def detail_card(entry: OnDisk) -> CardDetail:
    """Return every provider-backed field represented by the local file."""
    summary = summarize_card(entry)
    front = entry.file.front
    components = front.get("components")
    return CardDetail(
        **summary,
        body=entry.file.source,
        issue_type=_display_value(front.get("type")),
        status=_display_value(front.get("status")),
        priority=_display_value(front.get("priority")),
        assignee=_display_value(front.get("assignee")),
        reporter=_display_value(front.get("reporter")),
        components=(
            [str(item) for item in components] if isinstance(components, (list, tuple)) else []
        ),
        created=_display_value(front.get("created")),
        updated=_display_value(front.get("updated")),
        due=_display_value(front.get("due")),
        parent=_display_value(front.get("parent")),
        url=_display_value(front.get("url")),
    )


# ---- mutations -------------------------------------------------------------


def create_card(
    workspace: str,
    title: str,
    *,
    column: str = "",
    issue_type: str = "",
    body: str = "",
    labels: Sequence[str] = (),
    parent: str = "",
    blocked_by: Sequence[str] = (),
    assigned_agent: str = "",
    agent_name: str = "mcp",
) -> CardSummary:
    """Draft a card. Entirely local -- see ``preview_sync``/``confirm_sync``
    in ``server.py`` for the only path that reaches a real tracker."""
    path = resolve_workspace(workspace)
    with exclusive_workspace(path), open_workspace(workspace) as ws:
        target = _pick_column(ws.columns, column)
        resolved_type = ws.provider.resolve_issue_type(ws.project.project_id, issue_type) if issue_type else None
        draft = IssueDraft(
            title=title,
            body=body,
            issue_type=resolved_type.name if resolved_type else "",
            column_id=target.column_id,
            column_name=target.name,
            labels=tuple(labels),
            parent_key=parent,
        )
        agent_block = markdown.format_agent_block(blocked_by=blocked_by, assigned_agent=assigned_agent)
        written = write_draft(ws.path, ws.project, target, draft, agent_block=agent_block)
        committed, warning = _commit(ws.path, f"mcp({agent_name}): create {written.stem}", (written,))
        tokens.invalidate(ws.path)
        summary = summarize_card(OnDisk(path=written, column_name=target.name, file=markdown.read(written)))
        return CardSummary(**{**summary, "committed": committed, "warning": warning})


def move_card(workspace: str, card_id: str, column: str, *, agent_name: str = "mcp") -> CardSummary:
    """Move a card, refusing if the dependency gate (``blocked-by``) says no.

    Local Markdown only -- ``Backend.move_task`` never contacts the provider;
    that only happens later, through ``confirm_sync``.
    """
    path = resolve_workspace(workspace)
    with exclusive_workspace(path), open_workspace(workspace) as ws:
        entry = dependencies.resolve_card(ws.on_disk, card_id)
        if entry is None:
            raise ProviderError(f"no card matching {card_id!r} in this workspace")
        issue_id = str(entry.file.front.get("id", "") or "")
        target = _match_column(ws.columns, column)

        allowed, reason = dependencies.agent_can_move(issue_id, ws.on_disk, ws.folders)
        if not allowed:
            raise ProviderError(f"cannot move {card_id}: {reason}")

        backend = ProviderBackend(ws.path, ws.provider, ws.project.remote(), column_style=ws.project.column_style)
        task = next((t for t in backend.get_tasks() if str(t.metadata.get("id", "") or "") == issue_id), None)
        if task is None:
            raise ProviderError(f"{card_id} could not be reloaded for the move")
        # move_task takes Task.column_id's own small internal int space, not
        # RemoteColumn.column_id (the provider's real string id) -- get_columns()
        # is the board's translation between the two.
        local_column = next((c for c in backend.get_columns() if c.name == target.name), None)
        if local_column is None:
            raise ProviderError(f"{target.name} could not be resolved for the move")
        result = backend.move_task(task, local_column.column_id)
        if not result.ok:
            raise ProviderError(result.message)

        moved = dependencies.resolve_card(disk.read_disk(ws.path, ws.project.provider, ws.project.remote()), card_id)
        if moved is None:
            raise ProviderError(f"{card_id} moved, but could not be reloaded")
        committed, warning = _commit(ws.path, f"mcp({agent_name}): move {card_id} -> {target.name}", (moved.path,))
        tokens.invalidate(ws.path)
        summary = summarize_card(moved)
        return CardSummary(**{**summary, "committed": committed, "warning": warning})


def set_dependency(
    workspace: str, card_id: str, blocked_by: Sequence[str], *, agent_name: str = "mcp"
) -> CardSummary:
    """Replace a card's ``blocked-by`` list. An empty sequence clears it."""
    return _rewrite_agent_block(workspace, card_id, blocked_by=blocked_by, agent_name=agent_name)


def assign_agent(workspace: str, card_id: str, assigned_agent: str, *, agent_name: str = "mcp") -> CardSummary:
    """Record which agent owns a card. Informational only -- see the module
    docstring on why this is not ``assignee`` and not an exclusive claim."""
    return _rewrite_agent_block(workspace, card_id, assigned_agent=assigned_agent, agent_name=agent_name)


def update_card(
    workspace: str,
    card_id: str,
    *,
    title: str | None = None,
    issue_type: str | None = None,
    labels: Sequence[str] | None = None,
    due: str | None = None,
    priority: str | None = None,
    body: str | None = None,
    agent_name: str = "mcp",
) -> CardSummary:
    """Edit the fields a human could also edit by hand and sync.

    Deliberately excludes ``assignee`` -- real tracker identity, reserved for
    a confirmed sync, never this local-only path. Unsupported fields are not
    pre-checked; ``kbn sync --dry-run`` already reports what a tracker cannot
    write, and duplicating that check here would just drift from it.
    """
    path = resolve_workspace(workspace)
    with exclusive_workspace(path), open_workspace(workspace) as ws:
        entry = dependencies.resolve_card(ws.on_disk, card_id)
        if entry is None:
            raise ProviderError(f"no card matching {card_id!r} in this workspace")

        issue = _reconstruct_issue(entry, ws.folders)
        updates: dict[str, object] = {}
        if title is not None:
            updates["title"] = title
        if issue_type is not None:
            updates["issue_type"] = issue_type
        if labels is not None:
            updates["labels"] = tuple(labels)
        if due is not None:
            updates["due_date"] = _parse_date(due) if due else None
        if priority is not None:
            updates["priority"] = priority
        if body is not None:
            updates["body"] = body
        issue = issue.model_copy(update=updates)

        rendered = markdown.render(
            issue,
            column_name=entry.column_name,
            provider=ws.project.provider,
            notes=entry.file.notes,
            comments=entry.file.comments,
            comment_drafts=entry.file.comment_drafts,
            include_comment_region=entry.file.has_comment_region,
            agent_block=entry.file.agent_block,
        )
        from pykantui.config.paths import write_text_atomic  # noqa: PLC0415 - avoids an import cycle

        write_text_atomic(ensure_workspace_path(ws.path, entry.path), rendered)
        committed, warning = _commit(ws.path, f"mcp({agent_name}): update {card_id}", (entry.path,))
        tokens.invalidate(ws.path)
        summary = summarize_card(OnDisk(path=entry.path, column_name=entry.column_name, file=markdown.parse(rendered)))
        return CardSummary(**{**summary, "committed": committed, "warning": warning})


# ---- shared internals -------------------------------------------------------


def _rewrite_agent_block(
    workspace: str,
    card_id: str,
    *,
    blocked_by: Sequence[str] | None = None,
    assigned_agent: str | None = None,
    agent_name: str = "mcp",
) -> CardSummary:
    path = resolve_workspace(workspace)
    with exclusive_workspace(path), open_workspace(workspace) as ws:
        entry = dependencies.resolve_card(ws.on_disk, card_id)
        if entry is None:
            raise ProviderError(f"no card matching {card_id!r} in this workspace")

        current = markdown.parse_agent_block(entry.file.agent_block)
        new_blocked_by = list(blocked_by) if blocked_by is not None else current.get("blocked-by", "").split(", ")
        new_agent = assigned_agent if assigned_agent is not None else current.get("assigned-agent", "")
        agent_block = markdown.format_agent_block(
            blocked_by=[item for item in new_blocked_by if item.strip()],
            assigned_agent=new_agent,
            batch_id=current.get("batch-id", ""),
            batch_ref=current.get("batch-ref", ""),
        )

        issue = _reconstruct_issue(entry, ws.folders)
        rendered = markdown.render(
            issue,
            column_name=entry.column_name,
            provider=ws.project.provider,
            notes=entry.file.notes,
            comments=entry.file.comments,
            comment_drafts=entry.file.comment_drafts,
            include_comment_region=entry.file.has_comment_region,
            agent_block=agent_block,
        )
        from pykantui.config.paths import write_text_atomic  # noqa: PLC0415 - avoids an import cycle

        write_text_atomic(ensure_workspace_path(ws.path, entry.path), rendered)
        action = "set dependency" if blocked_by is not None else "assign"
        committed, warning = _commit(ws.path, f"mcp({agent_name}): {action} {card_id}", (entry.path,))
        tokens.invalidate(ws.path)
        summary = summarize_card(OnDisk(path=entry.path, column_name=entry.column_name, file=markdown.parse(rendered)))
        return CardSummary(**{**summary, "committed": committed, "warning": warning})


def _reconstruct_issue(entry: OnDisk, folders: dict[str, RemoteColumn]) -> RemoteIssue:
    """Rebuild the ``RemoteIssue`` a card's frontmatter already encodes.

    Used only to re-render a file whose ``pykantui:agent`` marker or a few
    editable fields are changing -- every other field carries through
    unchanged, so nothing else in the file moves.
    """
    front = entry.file.front
    labels = front.get("labels")
    components = front.get("components")
    column = folders.get(entry.column_name)
    return RemoteIssue(
        issue_id=str(front.get("id", "") or ""),
        key=str(front.get("key", "") or ""),
        title=str(front.get("title", "") or ""),
        column_id=column.column_id if column is not None else "",
        body=entry.file.source,
        issue_type=str(front.get("type", "") or ""),
        status=str(front.get("status", "") or ""),
        priority=str(front.get("priority", "") or ""),
        assignee=str(front.get("assignee", "") or ""),
        reporter=str(front.get("reporter", "") or ""),
        labels=tuple(str(item) for item in labels) if isinstance(labels, (list, tuple)) else (),
        components=(
            tuple(str(item) for item in components) if isinstance(components, (list, tuple)) else ()
        ),
        created_at=_parse_datetime(front.get("created")),
        updated_at=_parse_datetime(front.get("updated")),
        due_date=_parse_date(front.get("due")),
        parent_key=str(front.get("parent", "") or ""),
        url=str(front.get("url", "") or ""),
    )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _display_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value or "")


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return date.fromisoformat(value.strip()[:10])


def _pick_column(columns: list[RemoteColumn], wanted: str) -> RemoteColumn:
    """Match by name, folder or group; default to the first "not started"
    column when nothing was asked for -- mirrors ``commands/new.py``."""
    if not columns:
        raise ProviderError("this project has no columns")
    if wanted:
        return _match_column(columns, wanted)
    for group in (COLUMN_TODO, COLUMN_BACKLOG):
        found = next((column for column in columns if column.group == group), None)
        if found is not None:
            return found
    return columns[0]


def _match_column(columns: list[RemoteColumn], wanted: str) -> RemoteColumn:
    needle = wanted.strip().lower()
    for column in columns:
        if needle in (column.name.lower(), slugify(column.name), column.group):
            return column
    names = ", ".join(column.name for column in columns)
    raise ProviderError(f"no column matching {wanted!r}", hint=f"Columns: {names}")


def _commit(workspace: Path, message: str, paths: tuple[Path, ...]) -> tuple[bool, str]:
    """Best-effort audit-trail commit. Never undoes an already-written file.

    Skipped silently when the workspace isn't a Git repository at all (a
    documented, supported ``kbn init --no-git`` state); attempted and
    reported as a warning, not raised, when Git is present but the commit
    itself fails -- the mutation already succeeded by the time this runs.
    """
    if not (workspace / ".git").exists():
        return False, ""
    try:
        if not git.is_dirty(workspace, paths=paths):
            return True, ""
        if git.commit(workspace, message, paths=paths):
            return True, ""
        return False, "the card was saved, but the local audit commit failed"
    except git.GitCommandError as error:
        return False, f"the card was saved, but the local audit commit failed: {error}"
