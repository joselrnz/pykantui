"""End-to-end board behaviour, driven through Textual's pilot.

These press real keys against a real widget tree. Movement is the behaviour the
whole app exists for, so it is tested through the UI rather than by calling the
backend directly.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from rich.text import Text
from textual.content import Content
from textual.pilot import Pilot
from textual.widgets import Static

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.core.workflows import (
    DONE_COLUMN,
    IN_PROGRESS_COLUMN,
    NEEDS_REVIEW_COLUMN,
    TODO_COLUMN,
    WAITING_COLUMN,
)
from pykantui.models import BoardLayout, MovementMode, Task
from pykantui.pages.confirm import ConfirmMoveScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.board import MIN_COLUMN_WIDTH, KanbanBoard
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.column import COLLAPSED_WIDTH, BoardColumn

SIZE = (140, 40)


def workflow_backend() -> JsonBackend:
    """Three cards in To Do, then one in each later column."""
    backend = JsonBackend()
    backend.create_task(Task(task_id=1, title="first", column_id=TODO_COLUMN, description="body"))
    backend.create_task(Task(task_id=2, title="second", column_id=TODO_COLUMN))
    backend.create_task(Task(task_id=3, title="third", column_id=TODO_COLUMN))
    backend.create_task(Task(task_id=4, title="doing", column_id=IN_PROGRESS_COLUMN))
    backend.create_task(Task(task_id=5, title="in review", column_id=NEEDS_REVIEW_COLUMN))
    backend.create_task(Task(task_id=6, title="parked", column_id=WAITING_COLUMN))
    backend.create_task(Task(task_id=7, title="done", column_id=DONE_COLUMN))
    return backend


def make_app(
    backend: JsonBackend,
    movement_mode: MovementMode = MovementMode.ADJACENT,
    confirm_moves: bool = False,
) -> KanbanApp:
    """Build an app for the movement tests.

    Confirmation is off here so these stay about the movement mechanics.
    ConfirmationTests covers the dialog, which is on by default in real use.
    """
    return KanbanApp(backend=backend, movement_mode=movement_mode, confirm_moves=confirm_moves)


class SyncNamedJsonBackend(JsonBackend):
    """Local test store with provider-style confirmation copy enabled."""

    supports_sync = True

    def display_kind(self) -> str:
        return "Jira"


#: Long enough for the slowest real move, short enough to fail a parked worker fast.
SETTLE_TIMEOUT = 15.0


async def settle(pilot: Pilot[None]) -> None:
    """Drain pending work, and give up rather than hang.

    Moves run in a Textual worker, so a bare pause can return before the move
    has landed. Do not call this while a modal is open — the worker is parked
    awaiting the dismissal and will never complete.

    The timeout is the point: an unexpected modal used to park this forever,
    and a suite that hangs looks identical to a suite that is slow. One run sat
    there for half an hour. Now it fails, and says which test did it.
    """
    await pilot.pause()
    try:
        await asyncio.wait_for(pilot.app.workers.wait_for_complete(), timeout=SETTLE_TIMEOUT)
    except TimeoutError:  # pragma: no cover - only on a real defect
        screen = type(pilot.app.screen).__name__
        raise AssertionError(
            f"workers did not finish within {SETTLE_TIMEOUT}s with {screen} on top. "
            "A modal is probably open and the move worker is parked awaiting its dismissal."
        ) from None
    await pilot.pause()


def layout(app: KanbanApp) -> dict[int, list[int]]:
    """The board as ``{column id: [task ids in render order]}``."""
    return {
        column.column_id: [card.task_.task_id for card in column.cards()] for column in app.query(BoardColumn).results()
    }


class MovementTests(unittest.IsolatedAsyncioTestCase):
    async def test_l_moves_the_focused_card_one_column_right(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            self.assertEqual(layout(app)[1], [1, 2, 3])

            await pilot.press("L")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [2, 3])
            self.assertEqual(layout(app)[2], [4, 1])

    async def test_h_moves_the_focused_card_back(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)
            await pilot.press("H")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [2, 3, 1])
            self.assertEqual(layout(app)[2], [4])

    async def test_the_moved_card_keeps_focus(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)

            focused = app.focused
            self.assertIsInstance(focused, TaskCard)
            assert isinstance(focused, TaskCard)
            self.assertEqual(focused.task_.task_id, 1)
            self.assertEqual(focused.task_.column_id, 2)

    async def test_the_move_is_written_to_the_backend(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)

        stored = backend.get_task_by_id(1)
        assert stored is not None
        self.assertEqual(stored.column_id, 2)
        self.assertIsNotNone(stored.started_at)

    async def test_moving_into_the_finish_column_stamps_the_task_done(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            for _ in range(4):  # To Do -> In Progress -> Needs Review -> Waiting -> Done
                await pilot.press("L")
                await settle(pilot)

        stored = backend.get_task_by_id(1)
        assert stored is not None
        self.assertEqual(stored.column_id, DONE_COLUMN)
        self.assertTrue(stored.finished)

    async def test_column_headers_show_the_live_count(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)

            counts = {column.column_id: column.task_count for column in app.query(BoardColumn).results()}

        self.assertEqual(counts, {1: 2, 2: 2, 3: 1, 4: 1, 5: 1})

    async def test_adjacent_mode_does_not_move_past_the_last_column(self) -> None:
        backend = JsonBackend()
        backend.create_task(Task(task_id=1, title="done already", column_id=DONE_COLUMN))
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)

            self.assertEqual(layout(app)[DONE_COLUMN], [1])


class JumpModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_kanban_layout_recovers_first_card_focus_before_jump_key(self) -> None:
        """A startup layout pass must not leave jump keys without a receiver."""
        app = make_app(backend=workflow_backend(), movement_mode=MovementMode.JUMP)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            board.selected = None
            app.screen.set_focus(None)

            app.set_board_layout(BoardLayout.KANBAN)
            await pilot.pause()
            await pilot.press("L")
            await pilot.pause()

            self.assertEqual(board.target_column, IN_PROGRESS_COLUMN)

    async def test_l_only_targets_and_enter_commits(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend, movement_mode=MovementMode.JUMP)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)

            await pilot.press("L")
            # The target is deliberately transient.  Waiting for every app
            # worker can outlive TARGET_TIMEOUT on a contended test runner.
            await pilot.pause()
            self.assertEqual(board.target_column, 2)
            self.assertEqual(layout(app)[1], [1, 2, 3])  # nothing has moved yet

            await pilot.press("enter")
            await settle(pilot)

            self.assertEqual(layout(app)[2], [4, 1])
            self.assertIsNone(board.target_column)

    async def test_repeated_l_walks_the_target_further(self) -> None:
        app = make_app(backend=workflow_backend(), movement_mode=MovementMode.JUMP)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)

            await pilot.press("L")
            await pilot.press("L")
            # A plain pause, not settle(): the jump-mode highlight clears itself
            # after TARGET_TIMEOUT, and settle() can outlast that window.
            await pilot.pause()
            self.assertEqual(board.target_column, 3)

            await pilot.press("enter")
            await settle(pilot)

            self.assertEqual(layout(app)[3], [5, 1])

    async def test_targeting_back_to_the_origin_cancels(self) -> None:
        app = make_app(backend=workflow_backend(), movement_mode=MovementMode.JUMP)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)

            await pilot.press("L")
            await pilot.press("H")
            await settle(pilot)

            self.assertIsNone(board.target_column)
            self.assertEqual(layout(app)[1], [1, 2, 3])


class DependencyGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_blocked_card_cannot_enter_the_start_column(self) -> None:
        backend = JsonBackend()
        backend.create_task(Task(task_id=1, title="blocker", column_id=1))
        backend.create_task(Task(task_id=2, title="blocked", column_id=1, blocked_by=[1]))
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j")  # focus the blocked card
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [1, 2])

        stored = backend.get_task_by_id(2)
        assert stored is not None
        self.assertEqual(stored.column_id, 1)

    async def test_finishing_the_blocker_releases_the_gate(self) -> None:
        backend = JsonBackend()
        backend.create_task(Task(task_id=1, title="blocker", column_id=1))
        backend.create_task(Task(task_id=2, title="blocked", column_id=1, blocked_by=[1]))
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            for _ in range(4):  # blocker all the way to Done
                await pilot.press("L")
                await settle(pilot)

            # Focus the blocked card directly rather than navigating: with only
            # two cards left, h cycles between the two non-empty columns.
            app.query_one("#card-2", TaskCard).focus()
            await settle(pilot)
            await pilot.press("L")  # blocked card is now free to start
            await settle(pilot)

            self.assertEqual(layout(app)[IN_PROGRESS_COLUMN], [2])


class ReorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_j_moves_a_card_down_within_its_column(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("J")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [2, 1, 3])

    async def test_k_moves_it_back_up(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("J")
            await settle(pilot)
            await pilot.press("K")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [1, 2, 3])

    async def test_k_at_the_top_does_nothing(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("K")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [1, 2, 3])


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_j_and_k_move_focus_without_moving_cards(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j")
            await settle(pilot)

            focused = app.focused
            assert isinstance(focused, TaskCard)
            self.assertEqual(focused.task_.task_id, 2)
            self.assertEqual(layout(app)[1], [1, 2, 3])

    async def test_focus_wraps_at_the_bottom_of_a_column(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            for _ in range(3):
                await pilot.press("j")
            await settle(pilot)

            focused = app.focused
            assert isinstance(focused, TaskCard)
            self.assertEqual(focused.task_.task_id, 1)

    async def test_l_lowercase_crosses_to_the_next_column(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("l")
            await settle(pilot)

            focused = app.focused
            assert isinstance(focused, TaskCard)
            self.assertEqual(focused.task_.task_id, 4)
            self.assertEqual(layout(app)[1], [1, 2, 3])


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    """The confirmation dialog, which is on by default.

    ``settle`` is deliberately not used while the dialog is open: the move
    worker is parked awaiting the dismissal, so waiting for workers to finish
    would deadlock.
    """

    def _app(self, backend: JsonBackend | None = None) -> KanbanApp:
        return KanbanApp(backend=backend or workflow_backend(), confirm_moves=True)

    async def test_l_opens_the_dialog_and_moves_nothing_yet(self) -> None:
        backend = workflow_backend()
        app = self._app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()

            self.assertIsInstance(app.screen, ConfirmMoveScreen)
            self.assertEqual(layout(app)[1], [1, 2, 3])

            stored = backend.get_task_by_id(1)
            assert stored is not None
            self.assertEqual(stored.column_id, 1)

            await pilot.press("escape")
            await settle(pilot)

    async def test_clicking_move_commits(self) -> None:
        backend = workflow_backend()
        app = self._app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()

            await pilot.click("#confirm-move")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [2, 3])
            self.assertEqual(layout(app)[2], [4, 1])

        stored = backend.get_task_by_id(1)
        assert stored is not None
        self.assertEqual(stored.column_id, 2)

    async def test_clicking_cancel_leaves_the_board_and_backend_alone(self) -> None:
        backend = workflow_backend()
        app = self._app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()

            await pilot.click("#confirm-cancel")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [1, 2, 3])
            self.assertEqual(layout(app)[2], [4])

        stored = backend.get_task_by_id(1)
        assert stored is not None
        self.assertEqual(stored.column_id, 1)
        self.assertIsNone(stored.started_at)

    async def test_escape_cancels(self) -> None:
        app = self._app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()
            await pilot.press("escape")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [1, 2, 3])

    async def test_enter_approves(self) -> None:
        app = self._app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)

            self.assertEqual(layout(app)[2], [4, 1])

    async def test_the_dialog_names_both_columns(self) -> None:
        app = self._app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()

            dialog = app.screen
            assert isinstance(dialog, ConfirmMoveScreen)
            self.assertEqual(dialog.card_title, "first")
            self.assertEqual(dialog.origin, "To Do")
            self.assertEqual(dialog.destination, "In Progress")

            await pilot.press("escape")
            await settle(pilot)

    async def test_sync_move_copy_says_the_save_happens_after_approval(self) -> None:
        backend = SyncNamedJsonBackend()
        backend.create_task(Task(task_id=1, title="currently synced", column_id=TODO_COLUMN))
        app = self._app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()

            dialog = app.screen
            assert isinstance(dialog, ConfirmMoveScreen)
            self.assertEqual(
                "After Move: an unsent edit is saved to Markdown. Sync sends it to Jira.",
                dialog.warning,
            )

            await pilot.press("escape")
            await settle(pilot)

    async def test_finishing_a_task_is_called_out_in_the_dialog(self) -> None:
        backend = JsonBackend()
        backend.create_task(Task(task_id=1, title="nearly there", column_id=WAITING_COLUMN))
        app = self._app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("L")
            await pilot.pause()

            dialog = app.screen
            assert isinstance(dialog, ConfirmMoveScreen)
            self.assertIn("finished", dialog.warning)

            await pilot.press("escape")
            await settle(pilot)

    async def test_a_blocked_move_is_refused_without_asking(self) -> None:
        backend = JsonBackend()
        backend.create_task(Task(task_id=1, title="blocker", column_id=1))
        backend.create_task(Task(task_id=2, title="blocked", column_id=1, blocked_by=[1]))
        app = self._app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("j")
            await pilot.pause()
            await pilot.press("L")
            await settle(pilot)

            self.assertNotIsInstance(app.screen, ConfirmMoveScreen)
            self.assertEqual(layout(app)[1], [1, 2])

    async def test_reordering_does_not_ask(self) -> None:
        app = self._app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("J")
            await settle(pilot)

            self.assertNotIsInstance(app.screen, ConfirmMoveScreen)
            self.assertEqual(layout(app)[1], [2, 1, 3])

    async def test_c_toggles_confirmation_off(self) -> None:
        app = self._app()

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("c")
            await pilot.pause()
            self.assertFalse(app.confirm_moves)

            await pilot.press("L")
            await settle(pilot)

            self.assertNotIsInstance(app.screen, ConfirmMoveScreen)
            self.assertEqual(layout(app)[2], [4, 1])


class CollapseTests(unittest.IsolatedAsyncioTestCase):
    """Collapsing a column shrinks it to a strip but keeps it on the board."""

    async def test_z_collapses_the_focused_cards_column(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            ready = app.query_one("#column-1", BoardColumn)
            self.assertFalse(ready.collapsed)

            await pilot.press("z")
            await settle(pilot)

            self.assertTrue(ready.collapsed)
            self.assertTrue(ready.has_class("collapsed"))

    async def test_z_again_expands_it(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("z")
            await settle(pilot)
            # Focus left the collapsed column, so walk back to it.
            app.query_one("#column-1", BoardColumn).cards()
            board = app.query_one(KanbanBoard)
            board.set_collapsed(app.query_one("#column-1", BoardColumn), False)
            await settle(pilot)

            self.assertFalse(app.query_one("#column-1", BoardColumn).collapsed)

    async def test_collapsing_moves_focus_out_of_the_column(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("z")
            await settle(pilot)

            focused = app.focused
            assert isinstance(focused, TaskCard)
            self.assertNotEqual(focused.task_.column_id, 1)

    async def test_the_cards_are_kept_not_discarded(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("z")
            await settle(pilot)

            self.assertEqual(layout(app)[1], [1, 2, 3])

        self.assertEqual(len(backend.tasks_in_column(1)), 3)

    async def test_the_strip_shows_the_count_and_the_name(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("z")
            await settle(pilot)

            strip = app.query_one("#column-1 .column-strip", Static)
            lines = str(strip.content).splitlines()

        # Toggle glyph, blank, count, blank, then the name one letter per line.
        self.assertEqual(lines[2], "3")
        self.assertEqual("".join(lines[4:]), "TO DO")

    async def test_navigation_skips_a_collapsed_column(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            board.set_collapsed(app.query_one("#column-2", BoardColumn), True)
            await settle(pilot)

            app.query_one("#card-1", TaskCard).focus()
            await settle(pilot)
            await pilot.press("l")
            await settle(pilot)

            focused = app.focused
            assert isinstance(focused, TaskCard)
            self.assertEqual(focused.task_.column_id, 3)

    async def test_a_collapsed_column_is_still_a_move_target(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            board.set_collapsed(app.query_one("#column-2", BoardColumn), True)
            await settle(pilot)

            app.query_one("#card-1", TaskCard).focus()
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)

            self.assertEqual(layout(app)[2], [4, 1])
            self.assertEqual(app.query_one("#column-2", BoardColumn).task_count, 2)

        stored = backend.get_task_by_id(1)
        assert stored is not None
        self.assertEqual(stored.column_id, 2)

    async def test_focus_does_not_follow_a_card_into_a_collapsed_column(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            board.set_collapsed(app.query_one("#column-2", BoardColumn), True)
            await settle(pilot)

            app.query_one("#card-1", TaskCard).focus()
            await settle(pilot)
            await pilot.press("L")
            await settle(pilot)

            focused = app.focused
            assert isinstance(focused, TaskCard)
            self.assertNotEqual(focused.task_.column_id, 2)

    async def test_the_last_open_column_cannot_be_collapsed(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            for column_id in (2, 3, 4, 5):
                board.set_collapsed(app.query_one(f"#column-{column_id}", BoardColumn), True)
            await settle(pilot)

            board.set_collapsed(app.query_one("#column-1", BoardColumn), True)
            await settle(pilot)

            self.assertFalse(app.query_one("#column-1", BoardColumn).collapsed)

    async def test_shift_z_expands_everything(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            for column_id in (2, 3, 4, 5):
                board.set_collapsed(app.query_one(f"#column-{column_id}", BoardColumn), True)
            await settle(pilot)

            await pilot.press("Z")
            await settle(pilot)

            self.assertFalse(any(column.collapsed for column in board.columns()))

    async def test_clicking_the_toggle_collapses(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-1 .column-toggle")
            await settle(pilot)

            self.assertTrue(app.query_one("#column-1", BoardColumn).collapsed)

    async def test_the_toggle_does_not_open_the_menu(self) -> None:
        """The caret is collapse, not a dropdown. The menu is right-click / ","."""
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.click("#column-1 .column-toggle")
            await settle(pilot)

            self.assertEqual(len(app.screen.query("#menu-options")), 0)

    async def test_collapse_is_persisted_by_the_json_backend(self) -> None:
        backend = workflow_backend()
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("z")
            await settle(pilot)

        stored = next(column for column in backend.get_columns() if column.column_id == 1)
        self.assertTrue(stored.collapsed)

    async def test_a_board_opened_with_a_collapsed_column_renders_it_collapsed(self) -> None:
        backend = workflow_backend()
        backend.set_column_collapsed(3, True)
        app = make_app(backend=backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)

            self.assertTrue(app.query_one("#column-3", BoardColumn).collapsed)
            self.assertFalse(app.query_one("#column-1", BoardColumn).collapsed)


class DialogPlacementTests(unittest.IsolatedAsyncioTestCase):
    """Modals sit in the middle of the screen, not the top left."""

    @staticmethod
    def _off_centre(dialog_region: object, screen_size: object) -> tuple[int, int]:
        from textual.geometry import Region, Size

        assert isinstance(dialog_region, Region)
        assert isinstance(screen_size, Size)
        horizontal = abs(dialog_region.x + dialog_region.width // 2 - screen_size.width // 2)
        vertical = abs(dialog_region.y + dialog_region.height // 2 - screen_size.height // 2)
        return horizontal, vertical

    async def test_the_confirm_dialog_is_centred(self) -> None:
        app = KanbanApp(backend=workflow_backend(), confirm_moves=True)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("L")
            await pilot.pause()

            dialog = app.screen.query_one("#confirm-dialog")
            horizontal, vertical = self._off_centre(dialog.region, app.screen.size)

            # One cell of slack for odd widths and heights.
            self.assertLessEqual(horizontal, 1)
            self.assertLessEqual(vertical, 1)

            await pilot.press("escape")
            await settle(pilot)

    async def test_the_edit_dialog_is_centred(self) -> None:
        app = make_app(backend=workflow_backend())

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await pilot.press("n")
            await pilot.pause()

            dialog = app.screen.query_one("#edit-dialog")
            horizontal, vertical = self._off_centre(dialog.region, app.screen.size)

            self.assertLessEqual(horizontal, 1)
            self.assertLessEqual(vertical, 1)

            await pilot.press("escape")
            await settle(pilot)


class ColumnSizingTests(unittest.IsolatedAsyncioTestCase):
    """Widths, across column counts a board is actually configured with.

    Textual's ``1fr`` hands each child the whole container once the columns stop
    fitting, which turns a ten-column board into one column per screen. These
    pin the explicit sizing that replaced it.
    """

    @staticmethod
    def _config(count: int) -> BoardConfig:
        return BoardConfig(
            columns=[ColumnConfig(column_id=index + 1, name=f"C{index + 1}", position=index) for index in range(count)],
            reset_column=1,
            start_column=2 if count > 1 else None,
            finish_column=count,
        )

    async def _widths(self, count: int, size: tuple[int, int]) -> list[int]:
        app = make_app(backend=JsonBackend(config=self._config(count)))
        async with app.run_test(size=size) as pilot:
            await settle(pilot)
            return [column.region.width for column in app.query(BoardColumn).results()]

    async def test_columns_share_the_width_when_they_fit(self) -> None:
        widths = await self._widths(4, (160, 20))

        self.assertEqual(len(widths), 4)
        self.assertEqual(len(set(widths)), 1)
        self.assertGreater(widths[0], MIN_COLUMN_WIDTH)

    async def test_a_wider_terminal_gives_wider_columns(self) -> None:
        narrow = await self._widths(4, (120, 20))
        wide = await self._widths(4, (200, 20))

        self.assertGreater(wide[0], narrow[0])

    async def test_ten_columns_stay_readable_instead_of_filling_the_screen(self) -> None:
        widths = await self._widths(10, (120, 20))

        self.assertEqual(len(widths), 10)
        self.assertTrue(all(width == MIN_COLUMN_WIDTH for width in widths))

    async def test_twelve_columns_all_render(self) -> None:
        widths = await self._widths(12, (100, 20))

        self.assertEqual(len(widths), 12)

    async def test_no_column_is_ever_given_the_whole_board(self) -> None:
        for count in (6, 8, 12):
            with self.subTest(count=count):
                widths = await self._widths(count, (100, 20))
                self.assertTrue(all(width < 100 for width in widths))

    async def test_collapsing_gives_the_freed_width_to_the_others(self) -> None:
        app = make_app(backend=JsonBackend(config=self._config(4)))

        async with app.run_test(size=(160, 20)) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            before = app.query_one("#column-2", BoardColumn).region.width

            board.set_collapsed(app.query_one("#column-1", BoardColumn), True)
            await settle(pilot)
            after = app.query_one("#column-2", BoardColumn).region.width

        self.assertGreater(after, before)

    async def test_a_collapsed_column_keeps_its_strip_width(self) -> None:
        app = make_app(backend=JsonBackend(config=self._config(4)))

        async with app.run_test(size=(160, 20)) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            board.set_collapsed(app.query_one("#column-1", BoardColumn), True)
            await settle(pilot)

            width = app.query_one("#column-1", BoardColumn).region.width

        self.assertEqual(width, COLLAPSED_WIDTH)

    async def test_a_two_digit_collapsed_count_is_geometrically_centered(self) -> None:
        backend = JsonBackend(config=self._config(4))
        for task_id in range(1, 21):
            backend.create_task(Task(task_id=task_id, title=f"Card {task_id}", column_id=2))
        app = make_app(backend=backend)

        async with app.run_test(size=(160, 20)) as pilot:
            await settle(pilot)
            board = app.query_one(KanbanBoard)
            board.set_collapsed(app.query_one("#column-2", BoardColumn), True)
            await settle(pilot)

            strip = app.query_one("#column-2 .column-strip", Static)
            count_width = len(str(app.query_one("#column-2", BoardColumn).task_count))
            content_width = strip.content_region.width

        # Equal spare cells on both sides means the two-character count can
        # sit on the strip's exact cell centre, rather than half a cell left.
        self.assertEqual(0, (content_width - count_width) % 2)


class RefreshFailureTests(unittest.IsolatedAsyncioTestCase):
    """A refresh that fails must not take the board down with it.

    The CLI entry points report a bad config and exit, but by the time the
    board is on screen there is state worth keeping: a config another process
    mangled, or a Jira call that failed, should be survivable.
    """

    async def test_a_broken_reload_notifies_instead_of_crashing(self) -> None:
        backend = workflow_backend()
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            with patch.object(type(backend), "reload_local", side_effect=ValueError("bad config")):
                await pilot.press("r")
                # The notification is emitted by the awaited refresh action.
                # Do not wait on unrelated workers until its timeout expires.
                await pilot.pause()

            self.assertTrue(app.is_running)
            self.assertTrue(any("Reload failed" in str(n.message) for n in app._notifications))

    async def test_a_working_refresh_still_reloads(self) -> None:
        backend = workflow_backend()
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("r")
            await settle(pilot)

            self.assertTrue(app.is_running)
            self.assertFalse(any("Reload failed" in str(n.message) for n in app._notifications))

    async def test_provider_markdown_is_literal_during_mount_and_refresh(self) -> None:
        """A Trello description is Markdown, not Rich terminal markup."""
        backend = JsonBackend()
        unsafe = "Watch [Loom recording](https://loom.example/123) then [unfinished"
        backend.create_task(
            Task(
                task_id=1,
                title="Trello [card] title",
                column_id=TODO_COLUMN,
                description=unsafe,
            )
        )
        app = make_app(backend)

        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            card = app.query_one(TaskCard)
            rendered_title = card.query_one(".card-title", Static).render()
            rendered_body = card.query_one(".card-body", Static).render()
            self.assertIsInstance(rendered_title, Text | Content)
            self.assertIsInstance(rendered_body, Text | Content)
            assert isinstance(rendered_title, Text | Content)
            assert isinstance(rendered_body, Text | Content)
            self.assertEqual("Trello [card] title", rendered_title.plain)
            self.assertEqual(unsafe, rendered_body.plain)

            await pilot.press("r")
            await settle(pilot)

            self.assertTrue(app.is_running)
            card = app.query_one(TaskCard)
            refreshed_body = card.query_one(".card-body", Static).render()
            self.assertIsInstance(refreshed_body, Text | Content)
            assert isinstance(refreshed_body, Text | Content)
            self.assertEqual(unsafe, refreshed_body.plain)
