"""Typed plans and reports exchanged by workspace synchronization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from pykantui.api import ResponseCache
from pykantui.i18n import translate as _
from pykantui.tracker.models import CommentDraft, IssueEdit, RemoteIssue

_FIELD_LABELS = {
    "title": "Summary",
    "body": "Description",
    "column_id": "Status",
    "assignee": "Assignee",
    "labels": "Labels",
    "components": "Components",
    "due_date": "Due Date",
    "priority": "Priority",
    "issue_type": "Type",
}


class ConflictResolution(StrEnum):
    """A reviewed decision for one field changed on both sides."""

    HOLD = "hold"
    PROVIDER = "provider"
    LOCAL = "local"


@dataclass
class PendingPush:
    """One local edit, and what the tracker currently thinks about it."""

    key: str
    previous: RemoteIssue
    edit: IssueEdit
    remote: RemoteIssue | None = None
    conflict: bool = False
    unchecked: bool = False

    def describe(self) -> str:
        fields = ", ".join(self.edit.touched())
        if self.conflict:
            return f"{self.key}: {fields}  (CONFLICT — changed on the tracker too)"
        if self.unchecked:
            return f"{self.key}: {fields}  (could not check for remote changes)"
        return f"{self.key}: {fields}"

    def describe_change(self) -> list[str]:
        """Return compact, value-aware lines for an approved provider edit."""
        lines = [f"• {self.key}"]
        for field_name in self.edit.touched():
            before = getattr(self.previous, field_name)
            wanted = _wanted_value(self.edit, field_name)
            lines.append(
                f"    {_(_FIELD_LABELS.get(field_name, field_name))}: "
                f"{_preview(before)} → {_preview(wanted)}"
            )
        return lines

    def describe_blocked(self) -> list[str]:
        """Return the local/provider values that make this edit unsafe."""
        reason = _("conflict") if self.conflict else _("provider check unavailable")
        lines = [f"! {self.key} · {reason}"]
        for field_name in self.edit.touched():
            wanted = _wanted_value(self.edit, field_name)
            remote = getattr(self.remote, field_name) if self.remote is not None else None
            label = _(_FIELD_LABELS.get(field_name, field_name))
            if field_name in self.conflicting_fields():
                lines.extend(
                    (
                        f"    {label}",
                        _("      provider: {value}").format(value=_preview(remote)),
                        _("      local: {value}").format(value=_preview(wanted)),
                    )
                )
            else:
                lines.append(f"    {label}: {_preview(wanted)}")
        return lines

    def conflicting_fields(self) -> tuple[str, ...]:
        """Fields whose provider and local values diverged from the baseline."""
        if not self.conflict or self.remote is None:
            return ()
        found: list[str] = []
        for field_name in self.edit.touched():
            before = getattr(self.previous, field_name)
            remote = getattr(self.remote, field_name)
            wanted = _wanted_value(self.edit, field_name)
            if remote != before and remote != wanted:
                found.append(field_name)
        return tuple(found)


@dataclass(frozen=True, slots=True)
class PendingCommentPush:
    """One append-only local comment waiting for confirmation."""

    key: str
    previous: RemoteIssue
    draft: CommentDraft

    def describe_change(self) -> list[str]:
        return [f"• {self.key}", f"    {_preview(self.draft.body)}"]


@dataclass(frozen=True, slots=True)
class InvalidCard:
    """A Markdown card held locally because its metadata is unsafe to send."""

    issue_id: str
    filename: str
    errors: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.filename}: {'; '.join(self.errors)}"


@dataclass
class SyncPlan:
    """Provider writes a sync proposes before sending anything."""

    pushes: list[PendingPush] = field(default_factory=list)
    creates: list[str] = field(default_factory=list)
    create_details: list[str] = field(default_factory=list, repr=False)
    create_previews: list[str] = field(default_factory=list)
    comment_pushes: list[PendingCommentPush] = field(default_factory=list)
    invalid: list[InvalidCard] = field(default_factory=list)

    def clean(self) -> list[PendingPush]:
        return [item for item in self.pushes if not item.conflict and not item.unchecked]

    def conflicts(self) -> list[PendingPush]:
        return [item for item in self.pushes if item.conflict]

    def unchecked(self) -> list[PendingPush]:
        return [item for item in self.pushes if item.unchecked]

    def is_empty(self) -> bool:
        return not self.pushes and not self.creates and not self.comment_pushes and not self.invalid

    def outbound_token(self) -> tuple[tuple[object, ...], ...]:
        """Return a stable, exact identity for this plan's writes."""
        pushes = tuple(
            (
                item.key,
                item.previous.model_dump_json(),
                item.edit.model_dump_json(),
                item.remote.model_dump_json() if item.remote is not None else None,
                item.conflict,
                item.unchecked,
            )
            for item in self.pushes
        )
        creates = tuple((title, details) for title, details in zip(self.creates, self.create_details, strict=True))
        comments = tuple(
            (
                "comment",
                item.key,
                item.previous.issue_id,
                item.draft.local_id,
                item.draft.model_dump_json(),
            )
            for item in self.comment_pushes
        )
        invalid = tuple((item.issue_id, item.filename, item.errors) for item in self.invalid)
        return pushes + creates + comments + invalid

    def describe(self) -> str:
        """Return one CLI-friendly preview with safe and blocked sections."""
        if self.is_empty():
            return "nothing to send"
        return "\n\n".join(
            section for section in (self.describe_sendable(), self.describe_blocked()) if section
        )

    def describe_sendable(self) -> str:
        """Describe only operations that normal Sync will send."""
        clean = self.clean()
        ready_count = len(self.creates) + len(clean) + len(self.comment_pushes)
        if not ready_count:
            return ""
        lines = [_("READY TO SEND ({count})").format(count=ready_count)]
        if self.creates:
            lines.append("  " + _("CREATE ({count})").format(count=len(self.creates)))
            for index, title in enumerate(self.creates):
                lines.append(f"    + {title}")
                if index < len(self.create_previews) and self.create_previews[index]:
                    lines += [f"        {line}" for line in self.create_previews[index].splitlines()]
        if clean:
            lines.append("  " + _("UPDATE ({count})").format(count=len(clean)))
            for item in clean:
                lines.extend(f"    {line}" for line in item.describe_change())
        if self.comment_pushes:
            lines.append(f"  COMMENT ({len(self.comment_pushes)})")
            for comment_item in self.comment_pushes:
                lines.extend(f"    {line}" for line in comment_item.describe_change())
        return "\n".join(lines)

    def describe_blocked(self) -> str:
        """Describe invalid, conflicting, and unverifiable operations."""
        blocked_count = len(self.invalid) + len(self.conflicts()) + len(self.unchecked())
        if not blocked_count:
            return ""
        lines = [_("BLOCKED ({count})").format(count=blocked_count)]
        lines += [f"  ! {item.describe()}" for item in self.invalid]
        for item in (*self.conflicts(), *self.unchecked()):
            lines.extend(f"  {line}" for line in item.describe_blocked())
        return "\n".join(lines)


ConfirmPush = Callable[[SyncPlan], bool]


@dataclass
class SyncReport:
    """Observable local and provider effects of one sync."""

    created: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    commented: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    cache: ResponseCache | None = None
    held: list[str] = field(default_factory=list)
    plan: SyncPlan | None = None
    declined: bool = False
    archived: list[str] = field(default_factory=list)
    considered: int = 0
    mine: int = 0

    def total_changes(self) -> int:
        return (
            len(self.created)
            + len(self.written)
            + len(self.moved)
            + len(self.deleted)
            + len(self.archived)
            + len(self.commented)
        )

    def summary(self) -> str:
        counts = (
            ("created", self.created),
            ("sent", self.pushed),
            ("commented", self.commented),
            ("accepted provider version", self.accepted),
            ("wrote", self.written),
            ("moved", self.moved),
            ("archived", self.archived),
            ("deleted", self.deleted),
            ("skipped", self.skipped),
            ("held", self.held),
        )
        parts = [f"{label} {len(items)}" for label, items in counts if items]
        if self.declined:
            parts.append("send declined")
        return ", ".join(parts) or "no changes"


def _wanted_value(edit: IssueEdit, field_name: str) -> object:
    """Return the intended value, including an explicit field clear."""
    if field_name in edit.cleared:
        return () if field_name in {"labels", "components"} else None
    return getattr(edit, field_name)


def _preview(value: object, limit: int = 72) -> str:
    """Render a value on one terminal-safe line without flooding the dialog."""
    if value in (None, "", (), []):
        return "—"
    if isinstance(value, (tuple, list)):
        text = ", ".join(str(item) for item in value) or "—"
    else:
        text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


__all__ = [
    "ConfirmPush",
    "ConflictResolution",
    "InvalidCard",
    "PendingCommentPush",
    "PendingPush",
    "SyncPlan",
    "SyncReport",
]
