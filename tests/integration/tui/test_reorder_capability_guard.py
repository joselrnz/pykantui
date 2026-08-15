"""Unsupported row ordering is inert instead of surfacing provider errors."""

from __future__ import annotations

import unittest

from textual.widgets import TextArea

from pykantui.models import BoardLayout, MoveResult, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.work_items import WorkItemsView
from tests.integration.tui.test_board_tui import settle
from tests.integration.tui.test_comments_ui import CommentsBackend, two_comments


class NoRowOrderBackend(JsonBackend):
    """Jira-shaped ordering capability with a spy at the backend boundary."""

    supports_reorder = False

    def __init__(self) -> None:
        super().__init__()
        self.reorder_calls = 0
        self.create_task(Task(task_id=1, title="first", column_id=1))
        self.create_task(Task(task_id=2, title="second", column_id=1))

    def display_kind(self) -> str:
        return "Jira"

    def reorder_task(self, task: Task, target_position: int) -> MoveResult:
        del task, target_position
        self.reorder_calls += 1
        return MoveResult.failure("Jira backend has no row order")


class NoRowOrderCommentsBackend(CommentsBackend):
    supports_reorder = False


def _reorder_notices(app: KanbanApp) -> list[str]:
    return [str(notice.message) for notice in app._notifications if "row order" in str(notice.message)]


class ReorderCapabilityGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_uppercase_j_and_k_do_nothing_when_backend_has_no_row_order(self) -> None:
        backend = NoRowOrderBackend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            before = [task.task_id for task in backend.get_tasks()]
            await pilot.press("J", "K")
            await settle(pilot)

            self.assertEqual(0, backend.reorder_calls)
            self.assertEqual(before, [task.task_id for task in backend.get_tasks()])
            self.assertEqual([], _reorder_notices(app))

    async def test_board_rejects_a_stale_reorder_message_before_the_backend_boundary(self) -> None:
        backend = NoRowOrderBackend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            card = app.board.card_by_id(1)
            assert card is not None
            await app.board.on_task_card_reordered(TaskCard.Reordered(card, 1))
            await pilot.pause()

            self.assertEqual(0, backend.reorder_calls)
            self.assertEqual([], _reorder_notices(app))

    async def test_uppercase_j_is_typed_into_a_comment_draft_not_sent_to_the_board(self) -> None:
        backend = NoRowOrderCommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            await settle(pilot)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)
            draft = view.query_one("#work-item-comment-draft", TextArea)
            draft.focus()
            await pilot.press("J")
            await pilot.pause()

            self.assertEqual("J", draft.text)
            self.assertEqual([], _reorder_notices(app))


if __name__ == "__main__":
    unittest.main()
