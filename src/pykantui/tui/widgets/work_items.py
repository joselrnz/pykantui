"""Dense work-item rows with an optional JiraTUI-style detail pane."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Offset
from textual.message import Message
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, Select, Static, TabbedContent, TextArea

from pykantui.core.work_items import WorkItemColumn
from pykantui.i18n import translate as _
from pykantui.models import Task, same_task_identity
from pykantui.tui.provider_links import open_provider_url, provider_issue_url
from pykantui.tui.widgets import card_fields
from pykantui.tui.widgets.comments import CommentsPane
from pykantui.tui.widgets.dropdowns import DateInput
from pykantui.tui.widgets.work_item_compose import compose_work_item_view
from pykantui.tui.widgets.work_item_detail import WorkItemDetailBase
from pykantui.tui.widgets.work_item_editors import (
    InlineDetailEditor,
    InlineInfoEditor,
    apply_item_type,
    apply_status_group,
)
from pykantui.tui.widgets.work_item_resize import WorkItemResizeMixin
from pykantui.tui.widgets.work_item_table import WorkItemTable

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class WorkItemsView(WorkItemDetailBase, WorkItemResizeMixin):
    """Rows view on its own, or rows plus selected-card detail in Split."""

    app: KanbanApp

    BINDINGS = [
        Binding("1", "focus_table", "Work Items", show=False),
        Binding("2", "focus_tab('info')", "Info", show=False),
        Binding("3", "focus_tab('details')", "Details", show=False),
        Binding("4", "focus_tab('comments')", "Comments", show=False),
        Binding("5", "focus_tab('related')", "Related", show=False),
        Binding("6", "focus_tab('attachments')", "Attachments", show=False),
        Binding("7", "focus_tab('links')", "Links", show=False),
        Binding("8", "focus_tab('subtasks')", "Subtasks", show=False),
        Binding("e", "edit", "Edit"),
        Binding("v,enter", "detail", "View", key_display="v"),
        Binding("comma", "row_menu", "Row menu", show=False),
        Binding("ctrl+o", "open_provider", "↗", show=False),
        Binding("ctrl+s", "save_inline", "Save", priority=True, show=False),
        Binding("escape", "home", "Kanban", key_display="Esc"),
        Binding("left_square_bracket", "shrink_list", "Narrow work items", show=False),
        Binding("right_square_bracket", "grow_list", "Widen work items", show=False),
        Binding("backslash", "reset_split", "Reset divider", show=False),
    ]

    class DetailRequested(Message):
        def __init__(self, task: Task, *, editing: bool) -> None:
            self.task = task
            self.editing = editing
            super().__init__()

    class SaveRequested(Message):
        """Ask the app to persist a validated sidebar draft locally."""

        def __init__(self, view: WorkItemsView, task: Task) -> None:
            self.view = view
            self.task = task
            super().__init__()

    class RowMenuRequested(Message):
        """Ask the app for the compact actions belonging to a clicked row."""

        def __init__(self, view: WorkItemsView, task: Task, anchor: Offset | None) -> None:
            self.view = view
            self.task = task
            self.anchor = anchor
            super().__init__()

    def __init__(self) -> None:
        super().__init__(id="work-items-view")
        # Kanban is the initial layout.  Deferring the row population avoids
        # constructing a second, hidden representation of every card during
        # startup; ``KanbanApp.set_board_layout`` refreshes us when Rows or
        # Split becomes visible.
        self.display = False
        self._tasks: dict[str, Task] = {}
        self._selected_key = ""
        self._rendered_columns: tuple[WorkItemColumn, ...] = ()
        self.list_percent = self.DEFAULT_LIST_PERCENT
        self.default_list_percent = self.DEFAULT_LIST_PERCENT
        self._split_width_initialized = False
        self._dragging_resizer = False
        self.editing = False
        self._draft: Task | None = None
        self._saving = False
        self._opening_editor = False
        self._resize_timer: Timer | None = None
        self._resize_pending = False

    def compose(self) -> ComposeResult:
        yield from compose_work_item_view()

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self._refresh_status_theme)
        self.query_one("#work-item-description", Static).border_title = _("Description")
        self.query_one("#work-item-info-summary", Static).border_title = _("Summary")
        self.query_one("#work-item-private-notes", Static).border_title = _("Private Markdown notes · local only")
        self.query_one("#work-item-info-edit", VerticalScroll).display = False
        self.query_one("#work-item-edit-scroll", VerticalScroll).display = False
        self.query_one("#work-item-edit-save", Button).display = False
        self.query_one("#work-item-edit-cancel", Button).display = False
        self.query_one("#work-item-edit-start", Button).disabled = not self.app.backend.can_edit_tasks()
        self.query_one("#work-item-comments-pane", CommentsPane).bind_backend(self.app.backend)
        self._initialize_split_width(
            self.app.backend.available_task_fields(),
            self.app.backend.editable_task_fields(),
            provider_backed=self.app.backend.supports_sync,
        )
        if self.display:
            self.refresh_tasks()
        self._apply_split_width()

    def _refresh_status_theme(self, _theme: Theme) -> None:
        """Rebuild Rich Status and Type cells whose colors resolve eagerly."""
        if self.display:
            self.refresh_tasks()

    @property
    def detail_visible(self) -> bool:
        return bool(self.query_one("#work-item-detail-pane", Vertical).display)

    @property
    def editor_active(self) -> bool:
        """Whether an inline draft is open or is still mounting its controls."""
        return self.editing or self._opening_editor

    @on(DataTable.RowHighlighted)
    def row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._select(str(event.row_key.value or ""))

    @on(DataTable.HeaderSelected)
    async def header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Sort Rows/Split by a clicked data header without losing selection."""
        if self.editor_active:
            return
        try:
            column = WorkItemColumn(str(event.column_key.value))
        except ValueError:
            return
        if self.app.view.set_column_sort(column):
            await self.app.apply_view()

    @on(WorkItemTable.ContextRequested)
    def row_context_requested(self, event: WorkItemTable.ContextRequested) -> None:
        """Select the clicked row and ask the app to show its action menu."""
        task = self._tasks.get(event.task_key)
        if task is None or self.editing:
            return
        self._select(str(task.task_id))
        self.post_message(self.RowMenuRequested(self, task, event.anchor))

    @on(WorkItemTable.OpenRequested)
    def row_open_requested(self, event: WorkItemTable.OpenRequested) -> None:
        """Open the double-clicked row in the existing detail popup."""
        task = self._tasks.get(event.task_key)
        if task is None or self.editing:
            return
        self._select(str(task.task_id))
        self.post_message(self.DetailRequested(task, editing=False))

    def _select(self, key: str) -> None:
        self._selected_key = key
        task = self._tasks.get(key)
        comments = self.query_one("#work-item-comments-pane", CommentsPane)
        comments.set_task(task)
        edit_buttons = self.query("#work-item-edit-start")
        if edit_buttons:
            edit_buttons.first(Button).disabled = task is None or not self.app.backend.can_edit_task(task)
        if task is None:
            self._clear_detail()
            return
        self._render_detail(task)
        if self.query_one("#work-item-tabs", TabbedContent).active == "work-item-comments-tab":
            comments.activate()

    async def action_edit(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        if not self.detail_visible:
            self.post_message(self.DetailRequested(task, editing=True))
            return
        await self.start_inline_edit()

    def action_row_menu(self) -> None:
        """Open the selected row's actions when right-click is unavailable."""
        task = self.selected_task()
        if task is not None and not self.editing:
            self.post_message(self.RowMenuRequested(self, task, None))

    @on(WorkItemTable.LinkRequested)
    def row_link_requested(self, event: WorkItemTable.LinkRequested) -> None:
        """Open only when the selected key visibly advertises a safe link."""
        task = self._tasks.get(event.task_key)
        if task is None:
            return
        provider_id = str(task.metadata.get("id", "") or "")
        if provider_id != event.provider_id or provider_issue_url(task) != event.url:
            return
        open_provider_url(self.app, event.url)

    def action_open_provider(self) -> None:
        """Open the selected provider issue from Rows or Split."""
        task = self.selected_task()
        if task is not None:
            self._open_provider_url(task)

    def _open_provider_url(self, task: Task) -> None:
        open_provider_url(self.app, provider_issue_url(task))

    def action_detail(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        if self.detail_visible:
            self.query_one("#work-item-tabs", TabbedContent).focus()
        else:
            self.post_message(self.DetailRequested(task, editing=False))

    async def action_home(self) -> None:
        if self.editing:
            await self.cancel_inline_edit()
            return
        self.app.action_home()

    async def start_inline_edit(self, requested: Task | None = None) -> None:
        """Turn the existing Split sidebar into an editor without a screen push."""
        if self.editing or self._saving or self._opening_editor:
            return
        self._opening_editor = True
        try:
            snapshot = requested or self.selected_task()
            if snapshot is None:
                return
            current = self.app.backend.get_task_by_id(snapshot.task_id)
            visible = self._tasks.get(str(snapshot.task_id))
            if (
                current is None
                or visible is None
                or not same_task_identity(snapshot, current)
                or not same_task_identity(snapshot, visible)
            ):
                return
            if not self.app.backend.can_edit_task(current):
                self.app.notify(
                    _("{provider} cards cannot be edited here").format(provider=self.app.backend.display_kind()),
                    severity="warning",
                    timeout=3,
                )
                return

            self._tasks[str(current.task_id)] = current
            self._select(str(current.task_id))
            self._draft = current.model_copy(deep=True)
            policy = self.app.editor_policy()
            info_host = self.query_one("#work-item-info-edit", VerticalScroll)
            detail_host = self.query_one("#work-item-detail-edit", Vertical)
            await info_host.remove_children()
            await detail_host.remove_children()
            await info_host.mount(InlineInfoEditor(self._draft, policy))
            await detail_host.mount(
                InlineDetailEditor(
                    self._draft,
                    policy=policy,
                    columns=self.app.column_choices(),
                    blockers=self.app.backend.get_tasks_by_ids(self._draft.blocked_by),
                    priorities=self.app.provider_values("priority"),
                    status_group=self.app.backend.column_group(self._draft.column_id),
                    available_fields=self.app.backend.available_task_fields(),
                )
            )

            self.editing = True
            self.query_one(DataTable).disabled = True
            self.query_one("#work-item-info-read", VerticalScroll).display = False
            info_host.display = True
            self.query_one("#work-item-detail-scroll", VerticalScroll).display = False
            self.query_one("#work-item-edit-scroll", VerticalScroll).display = True
            self.query_one("#work-item-edit-start", Button).display = False
            save = self.query_one("#work-item-edit-save", Button)
            save.label = policy.save_label()
            save.display = True
            self.query_one("#work-item-edit-cancel", Button).display = True
            tabs = self.query_one("#work-item-tabs", TabbedContent)
            tabs.active = "work-item-info-tab"
            self.query_one("#work-item-edit-summary", Input).focus()
            self.app._refresh_footer()
        finally:
            self._opening_editor = False

    async def cancel_inline_edit(self) -> None:
        """Discard the in-memory sidebar draft and keep the Split view open."""
        if not self.editing:
            return
        self._draft = None
        self._saving = False
        self.editing = False
        self._show_read_only_detail()
        await self.query_one("#work-item-info-edit", VerticalScroll).remove_children()
        await self.query_one("#work-item-detail-edit", Vertical).remove_children()
        self.query_one(DataTable).focus()
        self.app._refresh_footer()
        self._refresh_after_editor()

    async def action_save_inline(self) -> None:
        """Validate and submit the sidebar draft to the app's local save path."""
        if not self.editing or self._draft is None or self._saving:
            return
        summary = self.query_one("#work-item-edit-summary", Input)
        if not summary.disabled and not summary.value.strip():
            self.app.notify(_("A card needs a summary"), severity="warning", timeout=3)
            summary.focus()
            return

        due = self.query("#work-item-edit-due")
        if due and isinstance(due.first(), DateInput):
            due_field = due.first(DateInput)
            if due_field.value.strip() and DateInput.parse(due_field.value) is None:
                self.app.notify("YYYY-MM-DD", title=_("Due Date"), severity="warning", timeout=3)
                due_field.focus()
                return

        values: dict[str, object] = {"summary": summary.value}
        for field in (item for row in card_fields.ROWS for item in row if item.key != "summary"):
            found = self.query(f"#work-item-edit-{field.key.replace('_', '-')}")
            if not found:
                continue
            widget = found.first()
            if isinstance(widget, Select):
                values[field.key] = None if widget.value is Select.NULL else str(widget.value)
            elif isinstance(widget, Input):
                values[field.key] = widget.value
        card_fields.apply(self._draft, values)

        policy = self.app.editor_policy()
        if policy.allows("body"):
            self._draft.description = self.query_one("#work-item-edit-description", TextArea).text
        private = self.query("#work-item-edit-private-notes")
        if private:
            self._draft.metadata["private_notes"] = private.first(TextArea).text

        self._saving = True
        self.query_one("#work-item-edit-save", Button).disabled = True
        self.post_message(self.SaveRequested(self, self._draft))

    async def inline_save_succeeded(self) -> None:
        """Close the draft controls after the backend has saved Markdown."""
        self._draft = None
        self._saving = False
        self._show_read_only_detail()
        await self.query_one("#work-item-info-edit", VerticalScroll).remove_children()
        await self.query_one("#work-item-detail-edit", Vertical).remove_children()
        self.query_one(DataTable).focus()
        self.app._refresh_footer()
        self.editing = False
        self._refresh_after_editor()

    def inline_save_failed(self) -> None:
        """Leave the recoverable draft open after a rejected local save."""
        self._saving = False
        self.query_one("#work-item-edit-save", Button).disabled = False

    @on(Select.Changed, "#work-item-edit-status")
    def inline_status_changed(self, event: Select.Changed) -> None:
        """Keep the inline Status control colored as its selected workflow."""
        try:
            column_id = int(str(event.value))
        except (TypeError, ValueError):
            apply_status_group(event.select, "unknown")
            return
        apply_status_group(event.select, self.app.backend.column_group(column_id))

    @on(Input.Changed, "#work-item-edit-issue-type")
    def inline_type_changed(self, event: Input.Changed) -> None:
        """Keep a native Type input mapped to the shared visual semantics."""
        apply_item_type(event.input, event.value)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Keep the focused inline field fully visible in a short sidebar."""
        scroll_hosts = {"work-item-info-edit", "work-item-edit-scroll"}
        inside_editor_scroll = any(ancestor.id in scroll_hosts for ancestor in event.widget.ancestors)
        if self.editing and inside_editor_scroll:
            # After the refresh, not immediately: focus often lands while the
            # editor is still laying out, and a scroll computed from that
            # stale region aims wrong once and never corrects itself.
            self.call_after_refresh(event.widget.scroll_visible, animate=False, immediate=True)

    def _show_read_only_detail(self) -> None:
        self.query_one(DataTable).disabled = False
        self.query_one("#work-item-info-read", VerticalScroll).display = True
        self.query_one("#work-item-info-edit", VerticalScroll).display = False
        self.query_one("#work-item-detail-scroll", VerticalScroll).display = True
        self.query_one("#work-item-edit-scroll", VerticalScroll).display = False
        self.query_one("#work-item-edit-start", Button).display = True
        self.query_one("#work-item-edit-save", Button).display = False
        self.query_one("#work-item-edit-save", Button).disabled = False
        self.query_one("#work-item-edit-cancel", Button).display = False

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "work-item-edit-start":
            event.stop()
            await self.start_inline_edit()
        elif event.button.id == "work-item-edit-save":
            event.stop()
            await self.action_save_inline()
        elif event.button.id == "work-item-edit-cancel":
            event.stop()
            await self.cancel_inline_edit()

    def action_focus_table(self) -> None:
        self.query_one(DataTable).focus()

    def action_focus_tab(self, name: str) -> None:
        """Activate a detail tab using JiraTUI's 2–8 navigation contract."""
        tab_id = f"work-item-{name}-tab"
        tabs = self.query_one("#work-item-tabs", TabbedContent)
        if self.query(f"#{tab_id}"):
            # A focused input in the old pane emits ``TabPane.Focused`` after
            # the reactive ``active`` update and otherwise switches us back.
            # Clear that focus first, then focus the requested tab itself.
            self.screen.set_focus(None)
            tabs.active = tab_id
            tabs.get_tab(tab_id).focus()
            if name == "comments":
                self.query_one("#work-item-comments-pane", CommentsPane).activate()

    @on(TabbedContent.TabActivated, "#work-item-tabs")
    def tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Lazy-load a thread only when its Comments tab is actually shown."""
        comments_active = event.pane.id == "work-item-comments-tab"
        self.query_one("#work-item-edit-actions", Horizontal).display = not comments_active
        if comments_active:
            self.query_one("#work-item-comments-pane", CommentsPane).activate()

    @on(CommentsPane.CountChanged)
    def comment_count_changed(self, event: CommentsPane.CountChanged) -> None:
        """Keep the inline tab count tied to its selected local snapshot."""
        if event.pane.id != "work-item-comments-pane":
            return
        event.stop()
        tab = self.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab")
        tab.label = f"{_('Comments')} ({event.count}) 4"
