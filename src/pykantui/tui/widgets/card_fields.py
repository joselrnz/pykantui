"""The field set for a card, shared by the view and edit popups.

One definition of what a card shows, so the two dialogs cannot drift apart.
Modelled on jiratui's Details panel: Summary, then the people and state, then
the identifiers, then the dates, then the free text.

Fields the backend cannot write are disabled rather than hidden — a Jira board
is read-only except for transitions, and a field that vanishes is harder to
reason about than one that is visibly not editable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from textual.widget import Widget
from textual.widgets import Select

from pykantui.core.filters import PRIORITY_ORDER, project_of
from pykantui.i18n import translate as _
from pykantui.models import Task
from pykantui.tui.widgets.dropdowns import DateInput, LabelledInput, LabelledSelect

#: Priorities offered when a board has none of its own, in Jira's order.
DEFAULT_PRIORITIES = [name.title() for name in PRIORITY_ORDER[:6]]


class FieldKind(StrEnum):
    """Which control a field is drawn as."""

    TEXT = "text"
    COLUMN = "column"
    """A dropdown of the board's columns."""

    PRIORITY = "priority"
    """A dropdown of the priorities in use on this board."""

    DATE = "date"


@dataclass(frozen=True)
class Field:
    """One labelled control, and where its value comes from."""

    key: str
    title: str
    shortcut: str = ""
    editable: bool = False
    kind: FieldKind = FieldKind.TEXT


#: Laid out row by row, mirroring jiratui's Details panel.
ROWS: list[list[Field]] = [
    [Field("summary", "Summary", "*", editable=True)],
    [
        Field("assignee", "Assignee", "x", editable=True),
        Field("status", "Status", "z", editable=True, kind=FieldKind.COLUMN),
        Field("issue_type", "Type", editable=True),
    ],
    [
        Field("key", "Key"),
        Field("parent", "Parent"),
        Field("sprint", "Sprint"),
    ],
    [Field("project", "Project")],
    [
        Field("priority", "Priority", "y", editable=True, kind=FieldKind.PRIORITY),
        Field("reporter", "Reporter"),
    ],
    [
        Field("created", "Created"),
        Field("updated", "Last Update"),
        Field("due", "Due Date", editable=True, kind=FieldKind.DATE),
    ],
    [
        Field("resolved", "Resolved"),
        Field("resolution", "Resolution"),
    ],
    [Field("labels", "Labels", editable=True)],
    [Field("components", "Components", editable=True)],
    [Field("blocked", "Blocked by")],
    [Field("time_tracking", "Time Tracking")],
]

PROVIDER_FIELD = {
    "summary": "title",
    "status": "column_id",
    "assignee": "assignee",
    "issue_type": "issue_type",
    "priority": "priority",
    "due": "due_date",
    "labels": "labels",
    "components": "components",
}


def provider_field(field: Field) -> str:
    """Translate a visible field key to the provider capability name."""
    return PROVIDER_FIELD.get(field.key, field.key)


@dataclass(frozen=True, slots=True)
class EditorPolicy:
    """Immutable provider capabilities shared by both card editor screens."""

    provider_name: str = ""
    writable_fields: frozenset[str] | None = None
    private_notes: bool = False
    local_first: bool = False

    @property
    def is_provider(self) -> bool:
        return bool(self.provider_name)

    def allows(self, field_name: str) -> bool:
        return self.writable_fields is None or field_name in self.writable_fields

    def allows_field(self, field: Field) -> bool:
        return self.allows(provider_field(field))

    @property
    def description_title(self) -> str:
        if not self.provider_name:
            return _("Description")
        template = (
            "Description · sent to {provider}"
            if self.allows("body")
            else "Description · read-only from {provider}"
        )
        return _(template).format(provider=self.provider_name)

    def save_label(self, *, draft: bool = False) -> str:
        if not self.local_first:
            return _("Save")
        return _("Save draft locally") if draft else _("Save locally")


def value_of(field: Field, task: Task, columns: list[tuple[str, int]], blockers: list[Task]) -> str:
    """The current value of a field, as text."""
    metadata = task.metadata
    match field.key:
        case "summary":
            return task.title.splitlines()[-1]
        case "status":
            return next((name for name, cid in columns if cid == task.column_id), "")
        case "key":
            return str(metadata.get("jira_key") or metadata.get("key") or f"#{task.task_id}")
        case "project":
            return project_of(task)
        case "created":
            return task.created_at.strftime("%Y-%m-%d %H:%M")
        case "updated":
            return _stamp(metadata.get("updated"))
        case "due":
            return task.due_date.isoformat() if task.due_date else ""
        case "resolved":
            return task.finished_at.strftime("%Y-%m-%d") if task.finished_at else ""
        case "labels" | "components":
            values = metadata.get(field.key)
            return ", ".join(str(item) for item in values) if isinstance(values, list) else str(values or "")
        case "blocked":
            return ", ".join(f"#{t.task_id} {t.title.splitlines()[0]}" for t in blockers)
        case "time_tracking":
            return str(metadata.get("time_tracking") or "N/A")
        case _:
            return str(metadata.get(field.key) or "")


def _stamp(raw: object) -> str:
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d %H:%M")
    text = str(raw or "")
    return text.replace("T", " ")[:16] if text else ""


def build(
    field: Field,
    task: Task,
    *,
    columns: list[tuple[str, int]],
    blockers: list[Task],
    prefix: str,
    editable: bool,
    priorities: list[str],
) -> Widget:
    """One control for one field, enabled only if it is editable here."""
    widget_id = f"{prefix}-{field.key.replace('_', '-')}"
    current = value_of(field, task, columns, blockers)
    enabled = editable and field.editable

    widget: Widget
    match field.kind:
        case FieldKind.COLUMN:
            widget = LabelledSelect(
                options=[(name, str(cid)) for name, cid in columns],
                prompt=_("Column"),
                title=_(field.title),
                key=field.shortcut,
                widget_id=widget_id,
                value=str(task.column_id) if columns else Select.NULL,
            )
        case FieldKind.PRIORITY:
            options = priorities or DEFAULT_PRIORITIES
            widget = LabelledSelect(
                options=[(_(name), name) for name in options],
                prompt=_("Select a priority"),
                title=_(field.title),
                key=field.shortcut,
                widget_id=widget_id,
                value=current if current in options else Select.NULL,
            )
        case FieldKind.DATE:
            date_field = DateInput(title=_(field.title), key=field.shortcut, widget_id=widget_id)
            date_field.value = current
            widget = date_field
        case FieldKind.TEXT:
            text_field = LabelledInput(placeholder="", title=_(field.title), key=field.shortcut, widget_id=widget_id)
            text_field.value = current
            widget = text_field

    widget.disabled = not enabled
    if not enabled and field.editable:
        widget.tooltip = _("Read-only on this backend")
    return widget


def apply(task: Task, values: dict[str, Any]) -> None:
    """Write the editable fields back onto a task."""
    if "summary" in values and values["summary"]:
        task.title = str(values["summary"])
    if "status" in values and values["status"] is not None:
        task.column_id = int(values["status"])
    if "due" in values:
        task.due_date = _as_date(values["due"])
    for key in ("assignee", "issue_type", "priority"):
        if key in values:
            text = str(values[key] or "")
            if text:
                task.metadata[key] = text
            else:
                task.metadata.pop(key, None)
    if "labels" in values:
        labels = [part.strip() for part in str(values["labels"] or "").split(",") if part.strip()]
        if labels:
            task.metadata["labels"] = labels
        else:
            task.metadata.pop("labels", None)
    if "components" in values:
        components = [part.strip() for part in str(values["components"] or "").split(",") if part.strip()]
        if components:
            task.metadata["components"] = components
        else:
            task.metadata.pop("components", None)


def _as_date(raw: object) -> date | None:
    return DateInput.parse(str(raw or ""))
