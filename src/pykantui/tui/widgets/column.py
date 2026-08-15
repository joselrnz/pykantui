"""A board column: a header and a scrollable stack of cards."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive

from pykantui.models import Task
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.text import BoardLabel, BoardStatic

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


#: Width of a collapsed column, in cells. Its four-cell interior centers both
#: one- and two-digit counts instead of forcing two digits half a cell left.
COLLAPSED_WIDTH = 6

#: Longest column name rendered down the strip before it is cut short.
COLLAPSED_NAME_LIMIT = 14


class BoardColumn(Vertical):
    app: KanbanApp

    task_count: reactive[int] = reactive(0)

    #: Collapsed columns stay on the board and stay valid move targets. They
    #: just shrink to a strip, so a column you are not working in costs a few
    #: cells instead of a full share of the width.
    collapsed: reactive[bool] = reactive(False, init=False)

    def __init__(
        self,
        title: str,
        column_id: int,
        tasks: list[Task] | None = None,
        collapsed: bool = False,
    ) -> None:
        super().__init__(id=f"column-{column_id}")
        self.title = title
        self.column_id = column_id
        self.tasks = tasks or []
        self.can_focus = False
        self.set_reactive(BoardColumn.collapsed, collapsed)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="column-header"):
            yield BoardLabel(self.title, classes="column-title", markup=False)
            yield BoardLabel(self._toggle_glyph(), classes="column-toggle", markup=False)
        yield VerticalScroll(classes="column-body", can_focus=False)
        yield BoardStatic(self._strip_text(), classes="column-strip", markup=False)

    async def on_mount(self) -> None:
        await self.replace(self.tasks)
        self._apply_collapsed()

    # ---- rendering -----------------------------------------------------

    def _toggle_glyph(self) -> str:
        return "»" if self.collapsed else "«"

    def _strip_text(self) -> str:
        """The collapsed strip: a toggle, the count, then the name downward."""
        name = self.title.upper()[:COLLAPSED_NAME_LIMIT]
        return "\n".join([self._toggle_glyph(), "", str(self.task_count), ""] + list(name))

    def watch_task_count(self) -> None:
        if not self.is_mounted:
            return
        suffix = "" if self.task_count == 0 else f" ({self.task_count})"
        self.query_one(".column-title", BoardLabel).update(f"{self.title}{suffix}")
        self.query_one(".column-strip", BoardStatic).update(self._strip_text())

    def watch_collapsed(self) -> None:
        if self.is_mounted:
            self._apply_collapsed()

    def _apply_collapsed(self) -> None:
        self.set_class(self.collapsed, "collapsed")
        self.query_one(".column-header", Horizontal).display = not self.collapsed
        self.query_one(".column-body", VerticalScroll).display = not self.collapsed
        strip = self.query_one(".column-strip", BoardStatic)
        strip.display = self.collapsed
        strip.update(self._strip_text())
        self.query_one(".column-toggle", BoardLabel).update(self._toggle_glyph())

    @on(Click, ".column-toggle")
    @on(Click, ".column-strip")
    def toggle_from_click(self, event: Click) -> None:
        """The header chevron collapses; the collapsed strip reopens.

        The menu lives on right-click and ``,`` — it does not take this over.
        """
        event.stop()
        self.post_message(self.ToggleRequested(self))

    class ToggleRequested(Message):
        """Collapse state is the board's to change — it also owns focus."""

        def __init__(self, column: BoardColumn) -> None:
            self.column = column
            super().__init__()

    def cards(self) -> list[TaskCard]:
        return list(self.query(TaskCard).results())

    # ---- mutation ------------------------------------------------------

    async def replace(self, tasks: list[Task]) -> None:
        body = self.query_one(".column-body", VerticalScroll)
        await body.remove_children()
        cards = [TaskCard(task=task, row=row) for row, task in enumerate(tasks)]
        if cards:
            await body.mount(*cards)
        self.tasks = tasks
        self.task_count = len(tasks)

    async def sync(self, tasks: list[Task]) -> None:
        """Bring the column in line with ``tasks``, rebuilding as little as possible.

        A full rebuild on every refresh drops focus and makes the board flicker,
        so the common cases — same cards, one removed, one appended — are
        handled without remounting the survivors.
        """
        current = self.cards()
        current_ids = [card.task_.task_id for card in current]
        wanted_ids = [task.task_id for task in tasks]

        if current_ids == wanted_ids:
            self._refresh_in_place(current, tasks)
            return

        if _is_subsequence(wanted_ids, current_ids):
            wanted_id_set = set(wanted_ids)
            # Refresh survivors while their composed children are still
            # mounted. Awaited removals may expose a transient Textual DOM
            # state in which a survivor is present but its children are not.
            by_id = {
                card.task_.task_id: card
                for card in current
                if card.task_.task_id in wanted_id_set
            }
            self._refresh_in_place([by_id[task_id] for task_id in wanted_ids], tasks)
            for card in current:
                if card.task_.task_id not in wanted_id_set:
                    await card.remove()
            return

        if current_ids == wanted_ids[: len(current_ids)]:
            body = self.query_one(".column-body", VerticalScroll)
            new_cards = [
                TaskCard(task=task, row=row) for row, task in enumerate(tasks[len(current) :], start=len(current))
            ]
            if new_cards:
                await body.mount(*new_cards)
            self._refresh_in_place(current + new_cards, tasks)
            return

        await self.replace(tasks)

    def _refresh_in_place(self, cards: list[TaskCard], tasks: list[Task]) -> None:
        for row, (card, task) in enumerate(zip(cards, tasks, strict=True)):
            task.position = row
            card.row = row
            card.refresh_task(task)
        self.tasks = tasks
        self.task_count = len(tasks)

    async def add(self, task: Task, position: int | None = None) -> TaskCard:
        body = self.query_one(".column-body", VerticalScroll)
        row = self.task_count if position is None else max(0, min(position, self.task_count))
        task.position = row
        card = TaskCard(task=task, row=row)

        if row == self.task_count:
            await body.mount(card)
        else:
            await body.mount(card, before=row)
        self.task_count += 1
        self._renumber()
        return card

    async def remove_task(self, task_id: int) -> None:
        for card in self.cards():
            if card.task_.task_id == task_id:
                await card.remove()
                self.task_count -= 1
                break
        self._renumber()

    def move_card(self, card: TaskCard, target_position: int) -> None:
        body = self.query_one(".column-body", VerticalScroll)
        others = [other for other in self.cards() if other is not card]
        if not others:
            return
        if target_position <= 0:
            body.move_child(card, before=others[0])
        elif target_position >= len(others):
            body.move_child(card, after=others[-1])
        else:
            body.move_child(card, before=others[target_position])
        self._renumber()

    def _renumber(self) -> None:
        for row, card in enumerate(self.cards()):
            card.row = row
            card.task_.position = row


def _is_subsequence(subset: list[int], full: list[int]) -> bool:
    index = 0
    for value in full:
        if index < len(subset) and subset[index] == value:
            index += 1
    return index == len(subset)
