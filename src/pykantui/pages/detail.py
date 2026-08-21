"""Everything about one card, on double-click.

The same field set the edit popup uses, rendered read-only. Pressing Edit turns
the editable ones on in place rather than swapping to a different dialog, so
what you looked at is what you edit.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Select, Static, TabbedContent, TabPane, TextArea

from pykantui.core.work_items import WorkItemColumn
from pykantui.i18n import translate as _
from pykantui.models import Task
from pykantui.sync.base import Backend
from pykantui.tracker.models import ColumnGroup
from pykantui.tui.provider_links import ProviderIssueLink, open_provider_url, provider_issue_url
from pykantui.tui.status_styles import WORKFLOW_STATUS_CLASSES, workflow_status_class
from pykantui.tui.type_styles import WORK_ITEM_TYPE_CLASSES, work_item_type_class
from pykantui.tui.widgets import card_fields
from pykantui.tui.widgets.card_fields import ROWS, EditorPolicy, Field
from pykantui.tui.widgets.comments import CommentsPane
from pykantui.tui.widgets.work_item_fields import (
    detail_field_visible,
    editable_field_available,
)


class TaskDetailScreen(ModalScreen[Task | None]):
    """View a card, and optionally edit it in place.

    Dismisses with the edited task when saved, or ``None`` when closed.
    """

    BINDINGS = [
        Binding("escape,q", "close", "Close"),
        Binding("2", "focus_tab('info')", "Info", show=False, priority=True),
        Binding("4", "focus_tab('comments')", "Comments", show=False, priority=True),
        Binding("e", "start_editing", "Edit"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+o", "open_provider", "↗", show=False),
    ]

    editing: reactive[bool] = reactive(False, init=False)

    def __init__(
        self,
        task: Task,
        column_name: str,
        blockers: list[Task],
        blocking: list[Task],
        *,
        columns: list[tuple[str, int]] | None = None,
        priorities: list[str] | None = None,
        writable: bool = True,
        editing: bool = False,
        policy: EditorPolicy | None = None,
        available_fields: Collection[WorkItemColumn] | None = None,
        status_group: ColumnGroup | str = ColumnGroup.UNKNOWN,
        status_groups: Mapping[int, ColumnGroup | str] | None = None,
        comments_backend: Backend | None = None,
    ) -> None:
        super().__init__()
        self.task_ = task.model_copy(deep=True)
        self.column_name = column_name
        self.blockers = blockers
        self.blocking = blocking
        self.columns = columns or []
        self.priorities = priorities or []
        self.writable = writable
        self.policy = policy or EditorPolicy()
        self.available_fields = frozenset(WorkItemColumn) if available_fields is None else frozenset(available_fields)
        self.status_group = status_group
        self.status_groups = dict(status_groups or {})
        self.comments_backend = comments_backend
        self.set_reactive(TaskDetailScreen.editing, editing and writable)

    # ---- layout --------------------------------------------------------

    def compose(self) -> ComposeResult:
        dialog = Vertical(id="detail-dialog")
        dialog.border_title = self._key()
        dialog.border_subtitle = self.column_name
        with dialog:
            with Horizontal(id="detail-headline-row"):
                yield Static(self._status_line(), id="detail-headline")
                yield ProviderIssueLink(provider_issue_url(self.task_), id="detail-provider-link")
            with TabbedContent(initial="detail-info-tab", id="detail-tabs"):
                with TabPane(f"{_('Info')} 2", id="detail-info-tab"), VerticalScroll(id="detail-body"):
                    for row in ROWS:
                        fields = self._visible_fields(row)
                        if not fields:
                            continue
                        with Horizontal(classes="field-row"):
                            for field in fields:
                                yield self._field(field)
                    notes = TextArea(self.task_.description, id="detail-notes")
                    notes.border_title = self.policy.description_title
                    notes.read_only = not self.editing
                    yield notes
                    if self.policy.private_notes:
                        private = TextArea(
                            str(self.task_.metadata.get("private_notes", "") or ""),
                            id="detail-private-notes",
                        )
                        private.border_title = _("Private Markdown notes · local only")
                        private.read_only = not self.editing
                        yield private
                with TabPane(f"{_('Comments')} 4", id="detail-comments-tab"):
                    yield CommentsPane(
                        "detail",
                        backend=self.comments_backend,
                        task=self.task_,
                    )
            with Horizontal(id="detail-buttons"):
                yield Button(self._primary_label(), variant="primary", id="detail-primary")
                yield Button(_("Close"), id="detail-close")

    def _field(self, field: Field) -> Widget:
        widget = card_fields.build(
            field,
            self.task_,
            columns=self.columns,
            blockers=self.blockers,
            prefix="detail",
            editable=self.editing and self.writable,
            priorities=self.priorities,
        )
        if field.key == "status":
            self._apply_status_group(widget, self.status_group)
        elif field.key == "issue_type":
            self._apply_item_type(widget, card_fields.value_of(field, self.task_, self.columns, self.blockers))
        return widget

    def _visible_fields(self, row: list[Field]) -> list[Field]:
        """Use the same provider contract as Split for read and edit modes."""
        fields = [
            field
            for field in row
            if detail_field_visible(
                field,
                value=card_fields.value_of(field, self.task_, self.columns, self.blockers),
                available=self.available_fields,
            )
        ]
        if not (self.editing and self.policy.is_provider):
            return fields
        return [
            field
            for field in fields
            if (
                field.editable
                and self.policy.allows_field(field)
                and editable_field_available(field, self.available_fields)
            )
        ]

    def _primary_label(self) -> str:
        if not self.writable:
            return _("Read-only")
        if not self.editing:
            return _("Edit")
        return self.policy.save_label()

    def _key(self) -> str:
        key = self.task_.metadata.get("jira_key") or self.task_.metadata.get("key")
        return str(key) if key else f"Card #{self.task_.task_id}"

    def _status_line(self) -> str:
        parts = [f"[b]{self.column_name}[/b]"]
        if self.task_.finished:
            parts.append(_("[$success]finished[/]"))
        elif self.task_.started_at:
            parts.append(_("in progress"))
        else:
            parts.append(_("not started"))

        unfinished = [task for task in self.blockers if not task.finished]
        if unfinished:
            parts.append(_("[$error]blocked by {count}[/]").format(count=len(unfinished)))
        elif self.blocking:
            parts.append(_("[$warning]blocking {count}[/]").format(count=len(self.blocking)))

        days = self.task_.days_left
        if days is not None and days < 0:
            parts.append(_("[$error]{days}d overdue[/]").format(days=abs(days)))
        elif days == 0:
            parts.append(_("[$warning]due today[/]"))
        return "   ·   ".join(parts)

    # ---- editing -------------------------------------------------------

    def on_mount(self) -> None:
        # Applied after mounting, not only in build(): a Select re-enables
        # itself as it mounts, so a field set disabled at construction comes
        # back editable. This is the one place that decides.
        self._sync_compact_layout()
        self._sync_editable()

    def on_resize(self, _event: events.Resize) -> None:
        """Use every row without overlapping actions in a short terminal."""
        self._sync_compact_layout()

    def _sync_compact_layout(self) -> None:
        self.set_class(self.size.height < 24, "compact-detail")

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Reveal the focused field inside a compact detail dialog."""
        if any(ancestor.id == "detail-body" for ancestor in event.widget.ancestors):
            body = self.query_one("#detail-body", VerticalScroll)
            widget = event.widget
            # After the refresh, not immediately: focus can land while the
            # dialog is still laying out, and a scroll computed from that
            # stale region aims wrong once and never corrects itself.
            if widget.id == "detail-private-notes":
                self.call_after_refresh(lambda: body.scroll_end(animate=False, force=True, immediate=True))
            else:
                self.call_after_refresh(
                    lambda: body.scroll_to_widget(widget, animate=False, force=True, immediate=True)
                )

    def watch_editing(self) -> None:
        if self.is_mounted:
            self._sync_editable()

    def _sync_editable(self) -> None:
        """Enable exactly the fields that may be edited right now."""
        allowed = self.editing and self.writable
        for row in ROWS:
            for field in row:
                found = self.query(f"#detail-{field.key.replace('_', '-')}")
                if found:
                    supported = self.policy.allows_field(field)
                    supported = supported and editable_field_available(field, self.available_fields)
                    found.first().disabled = not (allowed and field.editable and supported)
                    if field.editable and not supported and self.policy.provider_name:
                        found.first().tooltip = f"{self.policy.provider_name} cannot update {field.title.lower()}"
        body_allowed = self.policy.allows("body")
        description = self.query_one("#detail-notes", TextArea)
        description.disabled = False
        description.read_only = not (allowed and body_allowed)
        if not body_allowed and self.policy.provider_name:
            description.tooltip = f"{self.policy.provider_name} cannot update the description"
        if self.policy.private_notes:
            private_notes = self.query_one("#detail-private-notes", TextArea)
            private_notes.disabled = False
            private_notes.read_only = not allowed
        self.query_one("#detail-primary", Button).label = self._primary_label()

    @on(Select.Changed, "#detail-status")
    def _status_changed(self, event: Select.Changed) -> None:
        """Keep an edited Status mapped to its normalized workflow group."""
        try:
            column_id = int(str(event.value))
        except (TypeError, ValueError):
            group: ColumnGroup | str = ColumnGroup.UNKNOWN
        else:
            group = self.status_groups.get(column_id, ColumnGroup.UNKNOWN)
        self._apply_status_group(event.select, group)

    @on(Input.Changed, "#detail-issue-type")
    def _type_changed(self, event: Input.Changed) -> None:
        """Keep an edited native Type mapped to shared visual semantics."""
        self._apply_item_type(event.input, event.value)

    @staticmethod
    def _apply_status_group(widget: Widget, group: ColumnGroup | str) -> None:
        widget.remove_class(*WORKFLOW_STATUS_CLASSES)
        widget.add_class(workflow_status_class(group))

    @staticmethod
    def _apply_item_type(widget: Widget, value: object) -> None:
        widget.remove_class(*WORK_ITEM_TYPE_CLASSES)
        widget.add_class(work_item_type_class(value))

    def action_start_editing(self) -> None:
        if not self.writable:
            self.notify(_("This backend is read-only"), severity="warning", timeout=3)
            return
        self.editing = True

    def action_save(self) -> None:
        if not self.editing:
            return
        values: dict[str, object] = {}
        for row in ROWS:
            for field in row:
                if not field.editable:
                    continue
                if not self.policy.allows_field(field):
                    continue
                found = self.query(f"#detail-{field.key.replace('_', '-')}")
                if not found:
                    continue
                widget = found.first()
                if isinstance(widget, Select):
                    values[field.key] = None if widget.value is Select.NULL else str(widget.value)
                elif isinstance(widget, Input):
                    values[field.key] = widget.value

        if "summary" in values and not str(values["summary"] or "").strip():
            self.notify(_("A card needs a summary"), severity="warning", timeout=3)
            return

        card_fields.apply(self.task_, values)
        if self.policy.allows("body"):
            self.task_.description = self.query_one("#detail-notes", TextArea).text
        if self.policy.private_notes:
            self.task_.metadata["private_notes"] = self.query_one("#detail-private-notes", TextArea).text
        self.dismiss(self.task_)

    # ---- input ---------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "detail-close":
            self.dismiss(None)
        elif event.button.id == "detail-primary" and self.editing:
            self.action_save()
        elif event.button.id == "detail-primary":
            self.action_start_editing()

    def action_focus_tab(self, name: str) -> None:
        """Activate Info or Comments without changing the popup lifecycle."""
        tab_id = f"detail-{name}-tab"
        tabs = self.query_one("#detail-tabs", TabbedContent)
        if not self.query(f"#{tab_id}"):
            return
        self.set_focus(None)
        tabs.active = tab_id
        tabs.get_tab(tab_id).focus()
        if name == "comments":
            self.query_one("#detail-comments-pane", CommentsPane).activate()

    @on(TabbedContent.TabActivated, "#detail-tabs")
    def tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Lazy-load comments on pointer and keyboard tab changes alike."""
        if event.pane.id == "detail-comments-tab":
            self.query_one("#detail-comments-pane", CommentsPane).activate()

    @on(CommentsPane.CountChanged)
    def comment_count_changed(self, event: CommentsPane.CountChanged) -> None:
        """Show provider comments and pending drafts in the popup tab count."""
        if event.pane.id != "detail-comments-pane":
            return
        event.stop()
        tab = self.query_one("#detail-tabs", TabbedContent).get_tab("detail-comments-tab")
        tab.label = f"{_('Comments')} ({event.count}) 4"

    def action_close(self) -> None:
        self.dismiss(None)

    def action_open_provider(self) -> None:
        """Open the cached provider page without changing the edit draft."""
        open_provider_url(self.app, provider_issue_url(self.task_))
