"""The MCP move gate: cross-card dependencies from the ``pykantui:agent`` marker.

Deliberately separate from :meth:`pykantui.models.task.Task.can_move_to`,
which reads ``Task.blocked_by`` -- a field that exists only on the local JSON
board and is wired into the TUI's live move path. This module reads a
provider-backed card's own parsed Markdown instead, and never imports
``models.task`` or ``sync.*``.
"""

from __future__ import annotations

from pykantui.tracker.models import ColumnGroup, RemoteColumn
from pykantui.workspace import markdown
from pykantui.workspace.disk import OnDisk

_FINISHED_GROUPS = (ColumnGroup.DONE, ColumnGroup.CANCELLED)

#: How many blocker names to name before summarising the rest.
_NAMED_LIMIT = 3


def agent_can_move(
    issue_id: str,
    on_disk: dict[str, OnDisk],
    folders: dict[str, RemoteColumn],
) -> tuple[bool, str]:
    """Whether ``issue_id`` may move, per its own ``blocked-by`` marker.

    A blocker id that does not resolve to any card in this workspace scan is
    skipped rather than treated as blocking -- a typo, or a key from another
    workspace, must never wedge a card immovable forever. Finished means
    "sitting in a column whose group is done or cancelled", read off the
    blocker's own current folder; there is no card-level finished flag for a
    ``RemoteIssue``.
    """
    entry = on_disk.get(issue_id)
    if entry is None:
        return True, ""

    attributes = markdown.parse_agent_block(entry.file.agent_block)
    blocker_ids = [item.strip() for item in attributes.get("blocked-by", "").split(",") if item.strip()]
    if not blocker_ids:
        return True, ""

    unfinished = [
        _label(blocker)
        for blocker_id in blocker_ids
        if (blocker := resolve_card(on_disk, blocker_id)) is not None
        if not _is_finished(blocker, folders)
    ]
    if not unfinished:
        return True, ""

    names = ", ".join(unfinished[:_NAMED_LIMIT])
    extra = len(unfinished) - _NAMED_LIMIT
    suffix = f" and {extra} more" if extra > 0 else ""
    return False, f"blocked by {len(unfinished)} unfinished card(s): {names}{suffix}"


def resolve_card(on_disk: dict[str, OnDisk], card_ref: str) -> OnDisk | None:
    """Match a card reference by issue id first, then by tracker key.

    The one lookup every MCP tool needs: a caller may know a card as its
    human key (``JPT-4``) or, for an unsynced draft, only its local id.
    """
    direct = on_disk.get(card_ref)
    if direct is not None:
        return direct
    return next(
        (entry for entry in on_disk.values() if str(entry.file.front.get("key", "")) == card_ref),
        None,
    )


def _is_finished(entry: OnDisk, folders: dict[str, RemoteColumn]) -> bool:
    column = folders.get(entry.column_name)
    return column is not None and column.group in _FINISHED_GROUPS


def _label(entry: OnDisk) -> str:
    front = entry.file.front
    return str(front.get("key") or front.get("title") or "")
