"""Inline editor widgets used by the Rows and Split work-item views."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import TextArea

from pykantui.core.work_items import WorkItemColumn
from pykantui.i18n import translate as _
from pykantui.models import Task
from pykantui.tracker.models import ColumnGroup
from pykantui.tui.status_styles import WORKFLOW_STATUS_CLASSES, workflow_status_class
from pykantui.tui.type_styles import WORK_ITEM_TYPE_CLASSES, work_item_type_class
from pykantui.tui.widgets import card_fields
from pykantui.tui.widgets.card_fields import EditorPolicy
from pykantui.tui.widgets.dropdowns import LabelledInput
from pykantui.tui.widgets.work_item_fields import editable_field_available


class InlineInfoEditor(Vertical):
    """Summary and Markdown bodies edited inside the existing detail pane."""

    def __init__(self, task: Task, policy: EditorPolicy) -> None:
        super().__init__(classes="work-item-info-editor-content")
        self._card = task
        self._policy = policy

    def compose(self) -> ComposeResult:
        summary = LabelledInput(
            placeholder="",
            title=_("Summary"),
            key="*",
            widget_id="work-item-edit-summary",
        )
        summary.value = self._card.title.splitlines()[-1]
        summary.disabled = not self._policy.allows("title")
        yield summary

        description = TextArea(self._card.description, id="work-item-edit-description")
        description.border_title = self._policy.description_title
        description.disabled = not self._policy.allows("body")
        yield description

        if self._policy.private_notes:
            private = TextArea(
                str(self._card.metadata.get("private_notes", "") or ""),
                id="work-item-edit-private-notes",
            )
            private.border_title = _("Private Markdown notes · local only")
            yield private


class InlineDetailEditor(Vertical):
    """Provider-supported structured fields for one sidebar draft."""

    def __init__(
        self,
        task: Task,
        *,
        policy: EditorPolicy,
        columns: list[tuple[str, int]],
        blockers: list[Task],
        priorities: list[str],
        status_group: ColumnGroup | str,
        available_fields: frozenset[WorkItemColumn],
    ) -> None:
        super().__init__(classes="work-item-detail-editor-content")
        self._card = task
        self._policy = policy
        self._columns = columns
        self._blockers = blockers
        self._priorities = priorities
        self._status_group = status_group
        self._available_fields = available_fields

    def compose(self) -> ComposeResult:
        for row in card_fields.ROWS:
            fields = [
                field
                for field in row
                if (
                    field.key != "summary"
                    and field.editable
                    and self._policy.allows_field(field)
                    and editable_field_available(field, self._available_fields)
                )
            ]
            if not fields:
                continue
            with Horizontal(classes="work-item-edit-field-row"):
                for field in fields:
                    widget = card_fields.build(
                        field,
                        self._card,
                        columns=self._columns,
                        blockers=self._blockers,
                        prefix="work-item-edit",
                        editable=True,
                        priorities=self._priorities,
                    )
                    if field.key == "status":
                        apply_status_group(widget, self._status_group)
                    elif field.key == "issue_type":
                        apply_item_type(widget, self._card.metadata.get("issue_type"))
                    yield widget


def apply_status_group(widget: Widget, group: ColumnGroup | str) -> None:
    """Apply one semantic workflow class without disturbing focus classes."""
    widget.remove_class(*WORKFLOW_STATUS_CLASSES)
    widget.add_class(workflow_status_class(group))


def apply_item_type(widget: Widget, value: object) -> None:
    """Apply one type-semantic class without disturbing focus classes."""
    widget.remove_class(*WORK_ITEM_TYPE_CLASSES)
    widget.add_class(work_item_type_class(value))
