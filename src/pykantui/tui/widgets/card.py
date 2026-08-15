"""The task card.

A card never touches the backend. It posts a message upward and lets the board
decide, which is what keeps every write on one code path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive

from pykantui.i18n import translate as _
from pykantui.models import MovementMode, Task
from pykantui.tui.provider_links import ProviderIssueLink, open_provider_url, provider_issue_url
from pykantui.tui.widgets.text import BoardLabel, BoardStatic
from pykantui.workspace.status import SyncStatus

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp

Direction = Literal["left", "right"]
Vertical_ = Literal["up", "down"]


class TaskCard(Vertical):
    """One task. Focusable; expands to show its description when focused."""

    app: KanbanApp

    BINDINGS = [
        Binding("H", "move('left')", "Move left", key_display="H"),
        Binding("L", "move('right')", "Move right", key_display="L"),
        Binding("K", "reorder('up')", "Move up", key_display="K"),
        Binding("J", "reorder('down')", "Move down", key_display="J"),
        Binding("e,enter", "edit", "Edit", key_display="e"),
        Binding("d", "delete", "⌦ Delete"),
        Binding("i", "show_blockers", "Blockers"),
        Binding("v", "show_detail", "View"),
        Binding("ctrl+o", "open_provider", "↗", show=False),
    ]

    expanded: reactive[bool] = reactive(False)
    # Named task_ because Textual's MessagePump already owns .task (the widget's
    # asyncio Task). Shadowing it breaks message processing in ways that only
    # show up at runtime.
    task_: reactive[Task] = reactive(Task(task_id=0, title="", column_id=0), init=False)

    class Focused(Message):
        def __init__(self, card: TaskCard) -> None:
            self.card = card
            super().__init__()

    class Target(Message):
        """Jump mode: nominate a column without committing to it yet."""

        def __init__(self, card: TaskCard, direction: Direction) -> None:
            self.card = card
            self.direction = direction
            super().__init__()

    class Moved(Message):
        def __init__(self, card: TaskCard, target_column: int) -> None:
            self.card = card
            self.target_column = target_column
            super().__init__()

    class Reordered(Message):
        def __init__(self, card: TaskCard, target_position: int) -> None:
            self.card = card
            self.target_position = target_position
            super().__init__()

    class Deleted(Message):
        def __init__(self, card: TaskCard) -> None:
            self.card = card
            super().__init__()

    class EditRequested(Message):
        def __init__(self, card: TaskCard) -> None:
            self.card = card
            super().__init__()

    class BlockersRequested(Message):
        def __init__(self, card: TaskCard) -> None:
            self.card = card
            super().__init__()

    class DetailRequested(Message):
        """Double-click, or ``v``: show everything about this card."""

        def __init__(self, card: TaskCard) -> None:
            self.card = card
            super().__init__()

    def __init__(self, task: Task, row: int) -> None:
        super().__init__(id=f"card-{task.task_id}")
        self.row = row
        self.can_focus = True
        self.can_focus_children = False
        self.set_reactive(TaskCard.task_, task)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-heading"):
            yield BoardLabel(self.task_.title, classes="card-title", markup=False)
            yield ProviderIssueLink(provider_issue_url(self.task_))
        yield BoardLabel(self.metadata_line(), classes="card-meta", markup=True)
        yield BoardStatic(self.task_.description, classes="card-body", markup=False)

    def on_mount(self) -> None:
        self.watch_expanded()

    # ---- rendering -----------------------------------------------------

    def sync_status(self) -> SyncStatus | None:
        """Whether this card agrees with the tracker, if there is one.

        ``None`` for a board with no tracker behind it -- a local JSON board
        has nothing to be out of step with, and a dot that never changes is
        just noise. Also ``None`` for a value this version does not recognise,
        so a file written by a newer one degrades quietly.
        """
        raw = self.task_.metadata.get("sync_status")
        if not raw:
            return None
        try:
            return SyncStatus(str(raw))
        except ValueError:
            return None

    def sync_marker(self) -> str:
        status = self.sync_status()
        return status.marker if status else ""

    def sync_label(self) -> str:
        status = self.sync_status()
        return status.label if status else ""

    def metadata_line(self) -> str:
        parts = []

        status = self.sync_status()
        if status is not None:
            # Leads the line, and carries the only colour on it: when scanning
            # a column you want to see which cards need attention before
            # reading any of their detail. The colour is a theme variable, so
            # it follows the palette rather than being a hardcoded green that
            # disappears on a light background.
            parts.append(status.markup())

        parts.append(_("age {days}d").format(days=self.task_.days_since_creation))

        days_left = self.task_.days_left
        if days_left is None:
            parts.append(_("no due date"))
        elif days_left < 0:
            parts.append(_("overdue {days}d").format(days=abs(days_left)))
        elif days_left == 0:
            parts.append(_("due today"))
        else:
            parts.append(_("due in {days}d").format(days=days_left))

        if self.task_.blocked_by:
            blockers = [task for task in self.app.backend.get_tasks_by_ids(self.task_.blocked_by) if not task.finished]
            parts.append(_("blocked ({count})").format(count=len(blockers)) if blockers else _("unblocked"))
        elif self.task_.blocking:
            parts.append(_("blocking {count}").format(count=len(self.task_.blocking)))

        return "  ".join(parts)

    def refresh_task(self, task: Task) -> None:
        self.task_ = task
        self.query_one(".card-title", BoardLabel).update(task.title)
        self.query_one(ProviderIssueLink).set_provider_url(provider_issue_url(task))
        self.query_one(".card-meta", BoardLabel).update(self.metadata_line())
        self.query_one(".card-body", BoardStatic).update(task.description)
        self.set_class(bool(self._unfinished_blockers()), "blocked")

    def _unfinished_blockers(self) -> list[Task]:
        if not self.task_.blocked_by:
            return []
        return [task for task in self.app.backend.get_tasks_by_ids(self.task_.blocked_by) if not task.finished]

    # ---- focus ---------------------------------------------------------

    def on_focus(self) -> None:
        self.expanded = True
        self.scroll_visible(animate=False)
        self.post_message(self.Focused(self))

    def on_blur(self) -> None:
        self.expanded = False

    def watch_expanded(self) -> None:
        if not self.is_mounted:
            return
        body = self.query_one(".card-body", BoardStatic)
        body.display = self.expanded and bool(self.task_.description)

    # ---- bindings ------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Grey out bindings that cannot work here.

        Cheaper than letting them fire and fail: the footer stops advertising
        an action the current backend or board position does not support.
        """
        backend = self.app.backend

        if action == "reorder":
            if not backend.supports_reorder:
                return False
            # A sort is a view, not an order: there is nowhere for a reorder to
            # write while one is on. Pick Manual to move cards again.
            if self.app.view.sorted:
                return False
        if action == "edit" and not backend.can_edit_tasks():
            return False
        if action == "delete" and not backend.can_delete_task(self.task_):
            return False

        if action == "move":
            column_ids = [column.column_id for column in self.app.visible_columns]
            if not column_ids:
                return False
            at_edge = (
                column_ids[0] == self.task_.column_id
                if parameters == ("left",)
                else column_ids[-1] == self.task_.column_id
            )
            if at_edge:
                # In jump mode the highlight wraps, so the edge is still useful.
                return self.app.movement_mode == MovementMode.JUMP
        return True

    def action_move(self, direction: Direction) -> None:
        if self.app.movement_mode == MovementMode.JUMP:
            self.post_message(self.Target(self, direction))
            return
        target = self.app.neighbour_column(self.task_.column_id, direction)
        self.post_message(self.Moved(self, target))

    def action_reorder(self, direction: Vertical_) -> None:
        target = self.row - 1 if direction == "up" else self.row + 1
        if target < 0:
            return
        self.post_message(self.Reordered(self, target))

    def action_edit(self) -> None:
        self.post_message(self.EditRequested(self))

    def action_delete(self) -> None:
        self.post_message(self.Deleted(self))

    def action_show_blockers(self) -> None:
        self.post_message(self.BlockersRequested(self))

    def action_show_detail(self) -> None:
        self.post_message(self.DetailRequested(self))

    def action_open_provider(self) -> None:
        """Open the cached provider issue without a refresh or API lookup."""
        open_provider_url(self.app, provider_issue_url(self.task_))

    def on_click(self, event: Click) -> None:
        """Double-click opens the card.

        Single clicks are left alone: the board uses mouse-down and mouse-up to
        drag cards between columns, and stealing the click would break that.
        """
        if event.button == 1 and event.chain >= 2:
            event.stop()
            self.post_message(self.DetailRequested(self))
