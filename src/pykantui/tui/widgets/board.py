"""The board.

Owns navigation, the pending-move state machine, and — importantly — the only
code path that writes a move. Keyboard and mouse both end up in
:meth:`KanbanBoard.commit_move`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual import work
from textual.binding import Binding
from textual.containers import HorizontalScroll
from textual.events import Click, MouseDown, MouseMove, MouseUp
from textual.geometry import Offset
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer

from pykantui.i18n import translate as _
from pykantui.models import Board, Task
from pykantui.pages.confirm import ConfirmMoveScreen
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.column import COLLAPSED_WIDTH, BoardColumn

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp

#: How long a jump-mode highlight survives without a confirmation.
# A jump-mode target waits for a second direction key or Enter. 1.2 seconds
# was short enough to disappear while a busy terminal was still painting the
# first key press; five seconds remains transient without racing normal input.
TARGET_TIMEOUT = 5.0

#: Narrowest a column gets before the board scrolls sideways instead.
MIN_COLUMN_WIDTH = 20


class KanbanBoard(HorizontalScroll):
    app: KanbanApp

    BINDINGS = [
        Binding("h,left", "navigate('left')", "Left", show=False),
        Binding("l,right", "navigate('right')", "Right", show=False),
        Binding("j,down", "navigate('down')", "Down", show=False),
        Binding("k,up", "navigate('up')", "Up", show=False),
        Binding("enter", "confirm", "Confirm move", priority=True),
        Binding("z", "toggle_collapse", "Collapse", priority=True),
        Binding("Z", "expand_all", "Expand all", priority=True),
        # "comma", not ",": Textual splits a binding string on commas, so the
        # literal character parses as an empty key and raises at import time.
        Binding("comma", "column_menu", "Menu", key_display=",", priority=True),
        Binding("escape", "cancel_target", "Cancel", show=False, priority=True),
    ]

    selected: reactive[Task | None] = reactive(None)
    target_column: reactive[int | None] = reactive(None, init=False)

    def __init__(self) -> None:
        super().__init__(id="board")

        # Rebuilds must not overlap. Two filter changes in quick succession run
        # as separate workers, and a second rebuild entering between the
        # remove_children() and the mount() of the first raises DuplicateIds.
        self._rebuilding = False
        self._rebuild_again = False
        self._timer: Timer | None = None
        self._dragging = False
        self._drop_position: int | None = None

    async def on_mount(self) -> None:
        await self.rebuild()

    # ---- construction --------------------------------------------------

    async def rebuild(self) -> None:
        """Rebuild every column, coalescing overlapping requests.

        Filtering asks for a rebuild on each keystroke, so two can overlap and
        mounting the same ids twice raises DuplicateIds. A lock is the obvious
        fix and the wrong one: this runs on the board's own message pump, and
        `remove_children` needs that pump to make progress. A second call that
        blocked waiting for the lock would block the pump, and the rebuild
        holding it could never finish — a deadlock that moved between tests
        depending on message timing.

        So a request that arrives mid-flight does not wait. It sets a flag and
        returns, and the rebuild already running does one more pass afterwards
        against the newer state.
        """
        if self._rebuilding:
            self._rebuild_again = True
            return

        self._rebuilding = True
        try:
            while True:
                self._rebuild_again = False
                await self._rebuild_once()
                if not self._rebuild_again:
                    break
        finally:
            self._rebuilding = False

    async def _rebuild_once(self) -> None:
        # Only take focus back if it was already ours. Filtering rebuilds
        # the board on every keystroke, and stealing focus would kick the
        # user out of the search box after the first letter.
        refocus = self._holds_focus()
        await self.remove_children()
        tasks = self.app.visible_tasks()
        grouped = self._tasks_by_column(tasks)
        for column in self.app.visible_columns:
            await self.mount(
                BoardColumn(
                    title=column.name,
                    column_id=column.column_id,
                    tasks=grouped.get(column.column_id, []),
                    collapsed=column.collapsed,
                )
            )
        self.size_columns()
        if refocus:
            self.focus_first_card()

    def _holds_focus(self) -> bool:
        """Whether the focused widget is this board or a card inside it."""
        focused = self.app.focused
        if focused is None:
            return True
        return focused is self or focused in self.query("*").results()

    async def refresh_board(self) -> None:
        """Re-sync every column from the backend, keeping focus where it was."""
        refocus = self._holds_focus()
        focused = self.selected.task_id if self.selected else None
        tasks = self.app.visible_tasks()
        grouped = self._tasks_by_column(tasks)
        columns = {column.column_id: column for column in self.columns()}

        if list(columns) != [column.column_id for column in self.app.visible_columns]:
            await self.rebuild()
            return

        for column_id, widget in columns.items():
            await widget.sync(grouped.get(column_id, []))

        if not refocus:
            return
        if focused is not None:
            card = self.card_by_id(focused)
            if card is not None:
                card.focus()
                return
        self.focus_first_card()

    @staticmethod
    def _tasks_by_column(tasks: list[Task]) -> dict[int, list[Task]]:
        """Group a filtered snapshot in one pass for any number of columns."""
        grouped: dict[int, list[Task]] = {}
        for task in tasks:
            grouped.setdefault(task.column_id, []).append(task)
        return grouped

    def on_resize(self) -> None:
        self.size_columns()

    def size_columns(self) -> None:
        """Give every column an explicit width.

        ``width: 1fr`` cannot express what a board needs. Once the columns stop
        fitting, fr units hand each child the *whole* container instead of
        shrinking, so the board silently becomes one column per screen. Sizing
        them here means they share the space while they fit and the board
        scrolls sideways when they do not — which is the normal case as soon as
        you configure eight or ten columns.
        """
        columns = self.columns()
        if not columns or self.size.width <= 0:
            return

        collapsed = [column for column in columns if column.collapsed]
        expanded = [column for column in columns if not column.collapsed]
        for column in collapsed:
            column.styles.width = COLLAPSED_WIDTH
        if not expanded:
            return

        available = max(0, self.size.width - COLLAPSED_WIDTH * len(collapsed))
        width = max(MIN_COLUMN_WIDTH, available // len(expanded))
        for column in expanded:
            column.styles.width = width

    def columns(self) -> list[BoardColumn]:
        return list(self.query(BoardColumn).results())

    def column_widget(self, column_id: int) -> BoardColumn | None:
        return next((column for column in self.columns() if column.column_id == column_id), None)

    def card_by_id(self, task_id: int) -> TaskCard | None:
        return next((card for card in self.query(TaskCard).results() if card.task_.task_id == task_id), None)

    def focus_first_card(self) -> None:
        """Focus the first card that is actually on screen.

        Cards inside a collapsed column are not displayed, and focusing a
        hidden widget strands the cursor somewhere the user cannot see.
        """
        for column in self.columns():
            if column.collapsed:
                continue
            cards = column.cards()
            if cards:
                self.can_focus = False
                cards[0].focus()
                return
        self.can_focus = True
        self.focus()

    # ---- collapse ------------------------------------------------------

    def action_toggle_collapse(self) -> None:
        """Collapse or expand the column holding the focused card."""
        if self.selected is None:
            columns = self.columns()
            if columns:
                self.set_collapsed(columns[0], not columns[0].collapsed)
            return
        column = self.column_widget(self.selected.column_id)
        if column is not None:
            self.set_collapsed(column, not column.collapsed)

    def action_expand_all(self) -> None:
        for column in self.columns():
            if column.collapsed:
                self.set_collapsed(column, False)

    def on_board_column_toggle_requested(self, event: BoardColumn.ToggleRequested) -> None:
        self.set_collapsed(event.column, not event.column.collapsed)

    def set_collapsed(self, column: BoardColumn, collapsed: bool) -> None:
        if collapsed and self._only_expanded_column(column):
            self.app.notify(_("At least one column has to stay open"), severity="warning", timeout=3)
            return

        column.collapsed = collapsed
        self.app.backend.set_column_collapsed(column.column_id, collapsed)
        self.size_columns()

        if collapsed:
            # Focus cannot stay inside a column that is no longer displayed.
            if self.selected is not None and self.selected.column_id == column.column_id:
                self._focus_nearest_expanded(column.column_id)
        else:
            cards = column.cards()
            if cards:
                cards[0].focus()

    def _only_expanded_column(self, column: BoardColumn) -> bool:
        return not any(other.column_id != column.column_id and not other.collapsed for other in self.columns())

    def _focus_nearest_expanded(self, from_column_id: int) -> None:
        """Move focus to the closest column that is still open."""
        columns = self.columns()
        ids = [item.column_id for item in columns]
        if from_column_id not in ids:
            self.focus_first_card()
            return

        start = ids.index(from_column_id)
        for offset in range(1, len(columns) + 1):
            for index in (start + offset, start - offset):
                if not 0 <= index < len(columns):
                    continue
                candidate = columns[index]
                if candidate.collapsed:
                    continue
                cards = candidate.cards()
                if cards:
                    cards[0].focus()
                    return
        self.focus_first_card()

    # ---- navigation ----------------------------------------------------

    def action_navigate(self, direction: Literal["up", "down", "left", "right"]) -> None:
        if self.selected is None:
            self.focus_first_card()
            return

        column = self.column_widget(self.selected.column_id)
        if column is None:
            return
        cards = column.cards()
        if not cards:
            return
        row = next((index for index, card in enumerate(cards) if card.task_.task_id == self.selected.task_id), 0)

        if direction in {"up", "down"}:
            step = -1 if direction == "up" else 1
            cards[(row + step) % len(cards)].focus()
            return

        column_ids = [item.column_id for item in self.app.visible_columns]
        if self.selected.column_id not in column_ids:
            return
        index = column_ids.index(self.selected.column_id)
        step = -1 if direction == "left" else 1

        # Skip empty and collapsed columns rather than stranding focus on one.
        for offset in range(1, len(column_ids) + 1):
            candidate = self.column_widget(column_ids[(index + step * offset) % len(column_ids)])
            if candidate is None or candidate.collapsed:
                continue
            neighbours = candidate.cards()
            if neighbours:
                neighbours[min(row, len(neighbours) - 1)].focus()
                return

    # ---- pending target (jump mode) ------------------------------------

    def on_task_card_focused(self, event: TaskCard.Focused) -> None:
        self.selected = event.card.task_

    def on_task_card_target(self, event: TaskCard.Target) -> None:
        origin = self.target_column if self.target_column is not None else event.card.task_.column_id
        candidate = self.app.neighbour_column(origin, event.direction)

        if candidate == event.card.task_.column_id:
            self.target_column = None
            return

        self.target_column = candidate
        widget = self.column_widget(candidate)
        if widget is not None:
            widget.scroll_visible(animate=False)
        self._restart_timer()

    def _restart_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(TARGET_TIMEOUT, self._clear_target)

    def _clear_target(self) -> None:
        self.target_column = None
        self._timer = None

    def watch_target_column(self, old: int | None, new: int | None) -> None:
        if old is not None:
            widget = self.column_widget(old)
            if widget is not None:
                widget.remove_class("targeted")
        if new is not None:
            widget = self.column_widget(new)
            if widget is not None:
                widget.add_class("targeted")
        self.app._refresh_footer()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "confirm":
            return self.target_column is not None
        return True

    def action_confirm(self) -> None:
        if self.target_column is not None and self.selected is not None:
            self.request_move(self.selected, self.target_column)

    def action_cancel_target(self) -> None:
        if self.target_column is not None:
            self.target_column = None

    # ---- the single write path -----------------------------------------

    def on_task_card_moved(self, event: TaskCard.Moved) -> None:
        self.request_move(event.card.task_, event.target_column)

    @work(exclusive=True)
    async def request_move(self, task: Task, target_column: int, target_position: int | None = None) -> None:
        """Ask before moving, then hand off to :meth:`commit_move`.

        Every column move goes through here. A worker rather than a plain
        coroutine because awaiting a modal needs one.
        """
        board = self.app.backend.get_active_board()

        # Check the gate before asking. There is no point offering a choice
        # about a move the backend is going to refuse anyway.
        allowed, reason = task.can_move_to(target_column, board, self.app.backend)
        if not allowed:
            self.target_column = None
            self.app.notify(reason, title=_("Move blocked"), severity="warning", timeout=5)
            return

        if self.app.confirm_moves:
            origin = self.column_widget(task.column_id)
            destination = self.column_widget(target_column)
            approved = await self.app.push_screen_wait(
                ConfirmMoveScreen(
                    title=task.title,
                    origin=origin.title if origin else str(task.column_id),
                    destination=destination.title if destination else str(target_column),
                    warning=self._move_warning(target_column, board),
                )
            )
            if not approved:
                self.target_column = None
                return

        await self.commit_move(task, target_column, target_position)

    def _move_warning(self, target_column: int, board: Board) -> str:
        """Spell out the side effect the move has beyond changing column."""
        if self.app.backend.supports_sync:
            return _("After Move: an unsent edit is saved to Markdown. Sync sends it to {provider}.").format(
                provider=self.app.backend.display_kind()
            )
        if not self.app.backend.writable:
            return _("This writes to {provider}.").format(provider=self.app.backend.display_kind())
        if target_column == board.finish_column:
            return _("This marks the task finished.")
        if target_column == board.reset_column:
            return _("This clears the start and finish dates.")
        return ""

    async def commit_move(self, task: Task, target_column: int, target_position: int | None = None) -> None:
        """Move ``task`` into ``target_column``.

        Order matters: validate, then write to the backend, and only touch the
        widget tree once the write succeeded. A rejected move leaves the board
        exactly as it was, so there is nothing to roll back.
        """
        board = self.app.backend.get_active_board()
        origin = task.column_id

        allowed, reason = task.can_move_to(target_column, board, self.app.backend)
        if not allowed:
            self.target_column = None
            self.app.notify(reason, title=_("Move blocked"), severity="warning", timeout=5)
            return

        result = self.app.backend.move_task(task, target_column, target_position)
        if not result.ok or result.task is None:
            self.target_column = None
            self.app.notify(result.message, title=_("Move failed"), severity="error", timeout=5)
            return

        moved = result.task
        origin_widget = self.column_widget(origin)
        destination = self.column_widget(moved.column_id)
        if origin_widget is None or destination is None:
            await self.refresh_board()
            return

        await origin_widget.remove_task(moved.task_id)
        card = await destination.add(moved, position=moved.position)

        self.selected = moved
        self.target_column = None
        self._refresh_blocked_flags()

        # A collapsed column is a legitimate destination — you can file a card
        # into Done without opening it — but focus must not follow it in there.
        if destination.collapsed:
            self.app.notify(_("Moved to {column}").format(column=destination.title), timeout=3)
            self._focus_nearest_expanded(destination.column_id)
        else:
            card.focus()
            if result.message:
                self.app.notify(result.message, title=_("Moved"), timeout=3)

    async def on_task_card_reordered(self, event: TaskCard.Reordered) -> None:
        # Bindings are capability-gated on the card, but a queued message may
        # outlive a backend/layout refresh. Recheck at the write boundary so
        # providers without a durable row-order field (such as Jira) stay
        # inert instead of showing a misleading "Reorder failed" error.
        if not self.app.backend.supports_reorder:
            return
        if self.app.view.sorted:
            self.app.notify(
                _("Reordering needs Manual sort — the board is showing a sorted view"),
                severity="warning",
                timeout=4,
            )
            return
        column = self.column_widget(event.card.task_.column_id)
        if column is None:
            return
        if event.target_position >= len(column.cards()):
            return

        result = self.app.backend.reorder_task(event.card.task_, event.target_position)
        if not result.ok or result.task is None:
            self.app.notify(result.message, title=_("Reorder failed"), severity="error", timeout=5)
            return

        column.move_card(event.card, event.target_position)
        self.selected = result.task
        event.card.focus()

    @work(exclusive=True)
    async def on_task_card_deleted(self, event: TaskCard.Deleted) -> None:
        task = event.card.task_
        if not self.app.backend.can_delete_task(task):
            self.app.notify(
                _("Delete failed"),
                title=_("Delete failed"),
                severity="error",
                timeout=5,
            )
            return
        if self.app.backend.delete_requires_confirmation(task):
            question = _("Delete cards") + f"?\n{task.title}"
            if not await self.app._confirm(question, _("Delete cards")):
                return
        result = self.app.backend.delete_task_if_current(task)
        if not result.ok:
            self.app.notify(result.message, title=_("Delete failed"), severity="error", timeout=5)
            return

        column = self.column_widget(event.card.task_.column_id)
        if column is not None:
            await column.remove_task(event.card.task_.task_id)
        self._refresh_blocked_flags()
        self.focus_first_card()

    def on_task_card_blockers_requested(self, event: TaskCard.BlockersRequested) -> None:
        blockers = [
            task for task in self.app.backend.get_tasks_by_ids(event.card.task_.blocked_by) if not task.finished
        ]
        if not blockers:
            self.app.notify(_("Nothing is blocking this task"), title=_("Blockers"), timeout=3)
            return

        for task in blockers:
            card = self.card_by_id(task.task_id)
            if card is not None:
                card.add_class("flash")
        self.set_timer(1.0, self._clear_flash)

    def _clear_flash(self) -> None:
        for card in self.query(TaskCard).results():
            card.remove_class("flash")

    def _refresh_blocked_flags(self) -> None:
        """A move can unblock other cards, so re-read their dependency state."""
        for card in self.query(TaskCard).results():
            latest = self.app.backend.get_task_by_id(card.task_.task_id)
            if latest is not None:
                card.refresh_task(latest)

    # ---- mouse ---------------------------------------------------------

    def action_column_menu(self) -> None:
        """The same menu as right-click, for a keyboard or a terminal that eats
        right-clicks. Acts on the focused card's column."""
        column = None
        if self.selected is not None:
            column = self.column_widget(self.selected.column_id)
        if column is None:
            columns = self.columns()
            column = columns[0] if columns else None
        if column is not None:
            self.post_message(self.ColumnMenuRequested(column, self.menu_anchor(column)))

    def on_click(self, event: Click) -> None:
        """Right-click opens the menu for whichever column was clicked."""
        if event.button != 3:
            return
        for column in self.columns():
            if column.region.contains_point(event.screen_offset):
                event.stop()
                self.post_message(self.ColumnMenuRequested(column, self.menu_anchor(column)))
                return

    class ColumnMenuRequested(Message):
        def __init__(self, column: BoardColumn, anchor: Offset | None = None) -> None:
            self.column = column
            self.anchor = anchor
            super().__init__()

    @staticmethod
    def menu_anchor(column: BoardColumn) -> Offset:
        """Just under the column's header, so the menu reads as its dropdown."""
        return Offset(column.region.x, column.region.y + 2)

    def on_mouse_down(self, event: MouseDown) -> None:
        # Only the left button drags. A right-click here would otherwise pick a
        # card up and leave it stuck to the pointer while the menu is open.
        if event.button != 1:
            return
        for card in self.query(TaskCard).results():
            if card.region.contains_point(event.screen_offset):
                self._dragging = True
                self._drop_position = None
                self.selected = card.task_
                card.focus()
                return

    def on_mouse_move(self, event: MouseMove) -> None:
        if not self._dragging or self.selected is None:
            return
        for column in self.columns():
            if not column.region.contains_point(event.screen_offset):
                continue
            self._drop_position = self._insertion_index(column, event.screen_offset.y)
            if column.column_id == self.selected.column_id:
                self.target_column = None
            else:
                self.target_column = column.column_id
                self._restart_timer()
            return

    async def on_mouse_up(self, event: MouseUp) -> None:
        del event
        if not self._dragging:
            return
        self._dragging = False

        if self.target_column is not None and self.selected is not None:
            self.request_move(self.selected, self.target_column, self._drop_position)
        elif self._drop_position is not None and self.selected is not None:
            card = self.card_by_id(self.selected.task_id)
            if card is not None and self._drop_position != card.row:
                self.post_message(TaskCard.Reordered(card, self._drop_position))
        self._drop_position = None

    @staticmethod
    def _insertion_index(column: BoardColumn, y: int) -> int:
        """Where a drop at screen row ``y`` should land, by card midpoints."""
        cards = column.cards()
        for index, card in enumerate(cards):
            if y < card.region.y + card.region.height / 2:
                return index
        return len(cards)
