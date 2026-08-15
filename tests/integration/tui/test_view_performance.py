"""Performance contracts for the board's alternative representations."""

from __future__ import annotations

import gc
import time
import tracemalloc
import unittest
import weakref
from collections.abc import Iterator
from unittest.mock import patch

from pykantui.models import BoardLayout, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.work_items import WorkItemsView


def large_backend(card_count: int = 45) -> JsonBackend:
    """Return enough cards to make duplicate hidden-view rendering expensive."""
    backend = JsonBackend()
    backend._tasks = [  # noqa: SLF001 - a fixture should not perform 300 persisted writes
        Task(
            task_id=index,
            title=f"Card {index:04d}",
            column_id=(index % 5) + 1,
            position=index // 5,
            description=f"Description for card {index:04d}",
        )
        for index in range(1, card_count + 1)
    ]
    return backend


class _CountingTasks(list[Task]):
    """List that exposes full scans without relying on wall-clock timing."""

    iterations = 0

    def __iter__(self) -> Iterator[Task]:
        self.iterations += 1
        return super().__iter__()


class ViewPerformanceTests(unittest.IsolatedAsyncioTestCase):
    def test_configured_theme_is_selected_before_widgets_mount(self) -> None:
        """A large widget tree must never mount under a throwaway theme."""
        app = KanbanApp(large_backend(), confirm_moves=False)

        self.assertEqual("cyberpunk", app.theme)

    async def test_apply_view_updates_only_the_visible_representation(self) -> None:
        """Filtering must not rebuild hundreds of widgets that are hidden."""
        app = KanbanApp(large_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            work_items = app.query_one(WorkItemsView)

            with (
                patch.object(app.board, "refresh_board", wraps=app.board.refresh_board) as refresh_board,
                patch.object(work_items, "refresh_tasks", wraps=work_items.refresh_tasks) as refresh_rows,
            ):
                await app.apply_view()

            self.assertEqual(1, refresh_board.call_count)
            self.assertEqual(0, refresh_rows.call_count)

            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            with (
                patch.object(app.board, "refresh_board", wraps=app.board.refresh_board) as refresh_board,
                patch.object(work_items, "refresh_tasks", wraps=work_items.refresh_tasks) as refresh_rows,
            ):
                await app.apply_view()

            self.assertEqual(0, refresh_board.call_count)
            self.assertEqual(1, refresh_rows.call_count)

    async def test_kanban_groups_visible_tasks_in_one_scan(self) -> None:
        """Eight or ten columns must not rescan every card per column."""
        app = KanbanApp(large_backend(), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            tasks = _CountingTasks(app.backend.get_tasks())
            with patch.object(app, "visible_tasks", return_value=tasks):
                await app.board.refresh_board()
            self.assertEqual(1, tasks.iterations)

            tasks.iterations = 0
            with patch.object(app, "visible_tasks", return_value=tasks):
                await app.board._rebuild_once()  # noqa: SLF001 - deterministic scan contract
            self.assertEqual(1, tasks.iterations)

    async def test_rows_with_no_blockers_materialise_provider_tasks_once(self) -> None:
        """An empty blocker lookup must not rebuild every provider Task."""
        backend = large_backend()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()

            with patch.object(backend, "get_tasks", wraps=backend.get_tasks) as get_tasks:
                app.query_one(WorkItemsView).refresh_tasks()

            self.assertEqual(1, get_tasks.call_count)

    async def test_repeated_large_card_cycles_have_bounded_retained_growth(self) -> None:
        """Warmed refresh/filter cycles must not retain each old render."""
        app = KanbanApp(large_backend(60), confirm_moves=False)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()

            async def cycle(index: int) -> None:
                # "card 000" keeps only 0001..0009, so every other cycle
                # removes and later recreates 51 real TaskCard widgets.
                app.view.card_filter.text = "card 000" if index % 2 else ""
                await app.apply_view()
                await pilot.pause()

            tracemalloc.start()
            try:
                for index in range(4):
                    await cycle(index)
                gc.collect()
                retained_before = tracemalloc.get_traced_memory()[0]

                started = time.perf_counter()
                for index in range(4, 16):
                    await cycle(index)
                elapsed = time.perf_counter() - started

                gc.collect()
                retained_after = tracemalloc.get_traced_memory()[0]
            finally:
                tracemalloc.stop()

            retained_growth = max(0, retained_after - retained_before)
            # A local Linux measurement retained 6.65 MiB after 12 measured
            # churn cycles even though every detached TaskCard was collectable
            # (verified independently below).  The 16 MiB ceiling is generous
            # to Textual/asyncio caches but still rejects retaining all 612
            # removed card widgets across the measured cycles.
            self.assertLess(retained_growth, 16 * 1024 * 1024)
            self.assertGreater(elapsed, 0)

    async def test_removed_task_cards_are_collectable_across_repeated_filter_churn(self) -> None:
        """A hidden filter result must not retain each previous widget generation."""
        app = KanbanApp(large_backend(60), confirm_moves=False)
        stale: list[weakref.ReferenceType[TaskCard]] = []

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()

            for _ in range(5):
                cards = list(app.query(TaskCard).results())
                self.assertEqual(60, len(cards))
                stale.extend(
                    weakref.ref(card)
                    for card in cards
                    if "card 000" not in card.task_.title.casefold()
                )
                del cards

                app.view.card_filter.text = "card 000"
                await app.apply_view()
                await pilot.pause()
                self.assertEqual(9, len(app.query(TaskCard)))

                app.view.card_filter.clear()
                await app.apply_view()
                await pilot.pause()

            # End on the restrictive result so the last recreated generation
            # is removed too, then prove no detached TaskCard remains alive.
            app.view.card_filter.text = "card 000"
            await app.apply_view()
            await pilot.pause()
            gc.collect()

            self.assertEqual(5 * 51, len(stale))
            self.assertTrue(all(reference() is None for reference in stale))
