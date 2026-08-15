"""Creating and editing a card.

Same idiom as the filter header: every field carries its own label in its
border, so there is no separate caption to drift away from the control.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Select, TextArea

from pykantui.core.work_items import WorkItemColumn
from pykantui.i18n import translate as _
from pykantui.models import Task
from pykantui.tracker.models import ColumnGroup
from pykantui.tui.status_styles import WORKFLOW_STATUS_CLASSES, workflow_status_class
from pykantui.tui.type_styles import WORK_ITEM_TYPE_CLASSES, work_item_type_class
from pykantui.tui.widgets import card_fields
from pykantui.tui.widgets.card_fields import DEFAULT_PRIORITIES, EditorPolicy
from pykantui.tui.widgets.dropdowns import DateInput, LabelledInput, LabelledSelect
from pykantui.tui.widgets.work_item_fields import editable_field_available

_FIELDS_BY_PROVIDER_NAME = {
    card_fields.provider_field(field): field
    for row in card_fields.ROWS
    for field in row
}


class TaskEditScreen(ModalScreen[Task | None]):
    """Returns the edited task, or ``None`` if the user cancelled."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(
        self,
        task: Task | None = None,
        *,
        column_id: int = 1,
        task_id: int = 0,
        columns: list[tuple[str, int]] | None = None,
        policy: EditorPolicy | None = None,
        available_fields: Collection[WorkItemColumn] | None = None,
        status_group: ColumnGroup | str = ColumnGroup.UNKNOWN,
        status_groups: Mapping[int, ColumnGroup | str] | None = None,
    ) -> None:
        super().__init__()
        self.editing = task is not None
        self.columns = columns or []
        self.policy = policy or EditorPolicy()
        self.available_fields = frozenset(WorkItemColumn) if available_fields is None else frozenset(available_fields)
        self.status_group = status_group
        self.status_groups = dict(status_groups or {})

        # task_, not task: ModalScreen is a MessagePump, which owns .task.
        self.task_ = (
            task.model_copy(deep=True)
            if task
            else Task(task_id=task_id, title="", column_id=column_id, created_at=datetime.now())
        )

    def compose(self) -> ComposeResult:
        dialog = Vertical(id="edit-dialog")
        dialog.border_title = _("Edit {key}").format(key=self._key()) if self.editing else _("New card")
        dialog.border_subtitle = self._column_name()
        with dialog:
            with VerticalScroll(id="edit-body"):
                # (*) marks the one required field, the way jiratui marks its
                # Summary. Everything else is optional and carries no hint.
                title = LabelledInput(
                    placeholder=_("What needs doing?"), title=_("Title"), key="*", widget_id="edit-title"
                )
                title.value = self.task_.title
                yield title

                identity = [name for name in ("assignee", "column_id", "issue_type") if self._allows_field(name)]
                if identity:
                    with Horizontal(id="edit-row", classes="edit-field-row"):
                        if "assignee" in identity:
                            yield self._text_field(_("Assignee"), "edit-assignee", "assignee")
                        if "column_id" in identity:
                            yield self._column_select()
                        if "issue_type" in identity:
                            yield self._text_field(_("Type"), "edit-issue-type", "issue_type")

                scheduling = [name for name in ("priority", "due_date") if self._allows_field(name)]
                if scheduling:
                    with Horizontal(id="edit-scheduling-row", classes="edit-field-row"):
                        if "priority" in scheduling:
                            priority = str(self.task_.metadata.get("priority", "") or "")
                            yield LabelledSelect(
                                options=[(_(name), name) for name in DEFAULT_PRIORITIES],
                                prompt=_("Select a priority"),
                                title=_("Priority"),
                                key="",
                                widget_id="edit-priority",
                                value=priority if priority else Select.NULL,
                            )
                        if "due_date" in scheduling:
                            due = DateInput(title=_("Due date"), key="", widget_id="edit-due")
                            due.value = self.task_.due_date.isoformat() if self.task_.due_date else ""
                            yield due

                if self._allows_field("labels"):
                    yield self._text_field(_("Labels"), "edit-labels", "labels")
                if self._allows_field("components"):
                    yield self._text_field(_("Components"), "edit-components", "components")

                notes = TextArea(self.task_.description, id="edit-notes")
                notes.border_title = self.policy.description_title
                yield notes

            with Horizontal(id="edit-buttons"):
                yield Button(
                    self.policy.save_label(draft=True),
                    variant="primary",
                    id="edit-save",
                )
                yield Button(_("Cancel"), id="edit-cancel")

    def _key(self) -> str:
        key = self.task_.metadata.get("jira_key") or self.task_.metadata.get("key")
        return str(key) if key else f"#{self.task_.task_id}"

    def _column_name(self) -> str:
        return next(
            (name for name, column_id in self.columns if column_id == self.task_.column_id),
            "",
        )

    def _column_select(self) -> LabelledSelect:
        field = LabelledSelect(
            options=[(name, str(column_id)) for name, column_id in self.columns],
            prompt=_("Column"),
            title=_("Column"),
            key="c",
            widget_id="edit-column",
            value=str(self.task_.column_id) if self.columns else Select.NULL,
        )
        self._apply_status_group(field, self.status_group)
        return field

    def _text_field(self, title: str, widget_id: str, metadata_key: str) -> LabelledInput:
        field = LabelledInput(placeholder="", title=title, key="", widget_id=widget_id)
        value = self.task_.metadata.get(metadata_key, "")
        if metadata_key in {"labels", "components"} and isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        field.value = str(value or "")
        if metadata_key == "issue_type":
            self._apply_item_type(field, value)
        return field

    def _allows_field(self, provider_name: str) -> bool:
        """Require both write support and the provider's read capability."""
        field = _FIELDS_BY_PROVIDER_NAME.get(provider_name)
        return bool(
            self.policy.allows(provider_name)
            and field is not None
            and editable_field_available(field, self.available_fields)
        )

    def on_mount(self) -> None:
        if self.policy.writable_fields is not None:
            due = self.query("#edit-due")
            if due:
                due.first().disabled = not self.policy.allows("due_date")
            self.query_one("#edit-notes", TextArea).disabled = not self.policy.allows("body")
        self.query_one("#edit-title", Input).focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Reveal the focused field inside a compact new-card dialog."""
        if any(ancestor.id == "edit-body" for ancestor in event.widget.ancestors):
            event.widget.scroll_visible(animate=False, force=True, immediate=True)

    @on(Select.Changed, "#edit-column")
    def _status_changed(self, event: Select.Changed) -> None:
        """Keep the new-card Status control colored after selection changes."""
        try:
            column_id = int(str(event.value))
        except (TypeError, ValueError):
            group: ColumnGroup | str = ColumnGroup.UNKNOWN
        else:
            group = self.status_groups.get(column_id, ColumnGroup.UNKNOWN)
        self._apply_status_group(event.select, group)

    @on(Input.Changed, "#edit-issue-type")
    def _type_changed(self, event: Input.Changed) -> None:
        """Keep the new-card native Type mapped to shared visual semantics."""
        self._apply_item_type(event.input, event.value)

    @staticmethod
    def _apply_status_group(widget: Widget, group: ColumnGroup | str) -> None:
        widget.remove_class(*WORKFLOW_STATUS_CLASSES)
        widget.add_class(workflow_status_class(group))

    @staticmethod
    def _apply_item_type(widget: Widget, value: object) -> None:
        widget.remove_class(*WORK_ITEM_TYPE_CLASSES)
        widget.add_class(work_item_type_class(value))

    # ---- input ---------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-save":
            self.action_save()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        del event
        self.action_save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        title = self.query_one("#edit-title", Input).value.strip()
        if not title:
            self.notify(_("A card needs a title"), severity="warning", timeout=3)
            self.query_one("#edit-title", Input).focus()
            return

        values: dict[str, object] = {"summary": title}
        for field_name, selector in (
            ("assignee", "#edit-assignee"),
            ("issue_type", "#edit-issue-type"),
            ("priority", "#edit-priority"),
            ("due", "#edit-due"),
            ("labels", "#edit-labels"),
            ("components", "#edit-components"),
        ):
            found = self.query(selector)
            if not found:
                continue
            widget = found.first()
            if isinstance(widget, Select):
                values[field_name] = None if widget.value is Select.NULL else str(widget.value)
            elif isinstance(widget, Input):
                values[field_name] = widget.value

        column = self.query("#edit-column")
        if column:
            column_widget = column.first()
            if isinstance(column_widget, Select):
                selected = column_widget.value
                values["status"] = None if selected is Select.NULL else str(selected)

        card_fields.apply(self.task_, values)
        if self.policy.allows("body"):
            self.task_.description = self.query_one("#edit-notes", TextArea).text

        self.dismiss(self.task_)
