"""Real Textual rendering contracts for provider-shaped large boards."""

from __future__ import annotations

import time
import unittest

from textual.widgets import DataTable

from pykantui.core.actions import Action, ActionKind
from pykantui.core.filters import BoardView, CardFilter, FilterState
from pykantui.models import BoardLayout
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.registry import specs
from pykantui.tracker.spec import ProviderSpec
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.work_items import WorkItemsView

from .load_fixtures import CARD_COUNT, PROVIDER_NAMES, tasks_for

PROVIDER_RENDER_COUNT = 75
# Two isolated Docker measurements rendered and validated all 1,000 TaskCard
# widgets in 54.0 and 52.5 seconds (58.8 and 57.2 seconds process wall time).
# Ninety seconds leaves substantial host/CI headroom without making this a
# meaningless timeout-only assertion. The coverage gate deliberately runs this
# test after its concurrent shards so the wall clock continues to measure the
# renderer rather than competition from three other instrumented processes.
THOUSAND_CARD_KANBAN_BUDGET_SECONDS = 90.0


def configured_fields(spec: ProviderSpec) -> dict[str, object]:
    return {
        field.configuration_key: f"configured-{field.name.value}"
        for field in spec.card_fields
        if field.configuration_key
    }


class SpecBackend(JsonBackend):
    """Real TUI backend surface carrying one provider's declared fields."""

    supports_issue_fields = True

    def __init__(self, spec: ProviderSpec, *, count: int) -> None:
        super().__init__()
        self.spec = spec
        self._tasks = tasks_for(spec, count=count)

    def display_kind(self) -> str:
        return self.spec.label

    def provider_filter_fields(self):  # type: ignore[no-untyped-def]
        return self.spec.filter_fields(configured_fields(self.spec))


class LargeTextualRendererTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_provider_spec_renders_a_large_kanban_rows_and_split_board(self) -> None:
        self.assertEqual(PROVIDER_NAMES, {spec.name for spec in specs()})
        self.assertEqual(
            {BoardLayout.KANBAN, BoardLayout.ROWS, BoardLayout.SPLIT},
            set(BoardLayout),
        )

        for spec in specs():
            with self.subTest(provider=spec.name):
                backend = SpecBackend(spec, count=PROVIDER_RENDER_COUNT)
                app = KanbanApp(backend, confirm_moves=False)
                async with app.run_test(size=(160, 50)) as pilot:
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                    cards = list(app.query(TaskCard).results())
                    self.assertEqual(PROVIDER_RENDER_COUNT, len(cards))
                    self.assertTrue(
                        all(str(card.task_.metadata["id"]).startswith(f"{spec.name}-") for card in cards)
                    )

                    app.set_board_layout(BoardLayout.ROWS)
                    await pilot.pause()
                    rows = app.query_one(WorkItemsView)
                    table = rows.query_one(DataTable)
                    self.assertEqual(PROVIDER_RENDER_COUNT, table.row_count)
                    self.assertFalse(rows.detail_visible)

                    app.set_board_layout(BoardLayout.SPLIT)
                    await pilot.pause()
                    self.assertEqual(PROVIDER_RENDER_COUNT, table.row_count)
                    self.assertTrue(rows.detail_visible)

    async def test_one_thousand_card_kanban_respects_the_regression_budget(self) -> None:
        backend = SpecBackend(specs()[0], count=CARD_COUNT)
        app = KanbanApp(backend, confirm_moves=False)
        started = time.perf_counter()

        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            cards = list(app.query(TaskCard).results())
            elapsed = time.perf_counter() - started

            self.assertEqual(CARD_COUNT, len(cards))
            self.assertEqual(
                {task.task_id for task in backend.get_tasks()},
                {card.task_.task_id for card in cards},
            )
            self.assertLess(elapsed, THOUSAND_CARD_KANBAN_BUDGET_SECONDS, elapsed)

    async def test_real_widgets_rebuild_large_kanban_rows_and_split_views(self) -> None:
        spec = specs()[0]
        backend = JsonBackend()
        backend._tasks = tasks_for(spec)
        backend.config.saved_filters["Release backend"] = CardFilter(
            text="release-target",
            states=[FilterState.HAS_NOTES],
            provider={"labels": "backend"},
        )
        app = KanbanApp(backend, confirm_moves=False)
        app.view = BoardView(card_filter=CardFilter(text="render-visible"))

        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            cards = list(app.query(TaskCard).results())
            self.assertEqual(100, len(cards))
            self.assertEqual(
                {task.task_id for task in app.visible_tasks()},
                {card.task_.task_id for card in cards},
            )

            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            work_items = app.query_one(WorkItemsView)
            table = work_items.query_one(DataTable)
            app.view.card_filter.clear()
            work_items.refresh_tasks()
            await pilot.pause()
            self.assertEqual(CARD_COUNT, table.row_count)

            visible = app.visible_tasks()
            selected = visible[777]
            table.move_cursor(row=777)
            await pilot.pause()
            self.assertEqual(selected.task_id, work_items.selected_task().task_id)  # type: ignore[union-attr]

            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            self.assertEqual(CARD_COUNT, table.row_count)
            self.assertEqual(selected.task_id, work_items.selected_task().task_id)  # type: ignore[union-attr]

            app._run_view_action(Action.of(ActionKind.SAVED, "Release backend"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            expected_saved = [
                task
                for task in backend.get_tasks()
                if "release-target" in task.title
                and "backend" in task.metadata["labels"]
                and task.description.strip()
            ]
            self.assertEqual(len(expected_saved), table.row_count)
            self.assertEqual(
                [task.task_id for task in expected_saved],
                [task.task_id for task in app.visible_tasks()],
            )

            # Applying a saved filter must not hand its nested list/dict to
            # mutable session state.
            app.view.card_filter.states.clear()
            app.view.card_filter.provider["labels"] = "ui"
            saved = backend.config.saved_filters["Release backend"]
            self.assertEqual([FilterState.HAS_NOTES], saved.states)
            self.assertEqual({"labels": "backend"}, saved.provider)


if __name__ == "__main__":
    unittest.main()
