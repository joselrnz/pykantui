"""Twenty-plus-card edge contracts shared by every shipped provider."""

from __future__ import annotations

import copy
import unittest

from textual.coordinate import Coordinate
from textual.widgets import DataTable

from pykantui.core.filters import BoardView, CardFilter, SortKey
from pykantui.core.work_items import WORK_ITEM_COLUMN_SPECS, WorkItemColumn
from pykantui.models import BoardLayout, MoveResult, Task
from pykantui.tracker.registry import specs
from pykantui.tracker.spec import ProviderSpec
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView

from .load_fixtures import PROVIDER_NAMES, tasks_for
from .test_provider_render_load import SpecBackend, configured_fields

EDGE_CARD_COUNT = 27
CONTROL_CHARACTERS = {
    *{chr(value) for value in (*range(32), *range(127, 160))},
    "\u2028",
    "\u2029",
}


def edge_tasks(spec: ProviderSpec) -> list[Task]:
    """Return 27 provider-shaped cards containing awkward but valid data."""
    tasks = tasks_for(spec, count=EDGE_CARD_COUNT)
    duplicate_titles = ("Same summary", "same SUMMARY", "Same summary")
    for index, task in enumerate(tasks):
        task.title = duplicate_titles[index % len(duplicate_titles)] if index < 9 else task.title
        task.metadata["status"] = ("To Do", "In Progress", "Done", "Unknown state")[index % 4]
        task.metadata["reporter"] = "" if index % 5 == 0 else f"Reporter {index % 3}"
        task.metadata["components"] = [] if index % 4 == 0 else ["API", f"Part {index % 3}"]
        if index % 6 == 0:
            for field in ("issue_type", "assignee", "priority", "labels", "components"):
                task.metadata.pop(field, None)
            task.due_date = None
        if index == 7:
            task.title = "[bold red]literal markup[/] · 日本語 · e\u0301 · " + "x" * 180
            task.metadata["key"] = f"{spec.name.upper()}-7\x1b\x00\x7f"
            task.metadata["assignee"] = "Alex\r\nInjected\u2028Again"
            task.metadata["labels"] = ["safe", "bad\x07label"]
        if index == EDGE_CARD_COUNT - 1:
            task.title = "Last visible card"
            task.metadata["key"] = f"{spec.name.upper()}-LAST"
    return tasks


class EdgeSpecBackend(SpecBackend):
    """Provider-accurate field contract with write spies."""

    def __init__(self, spec: ProviderSpec) -> None:
        super().__init__(spec, count=EDGE_CARD_COUNT)
        self._tasks = edge_tasks(spec)
        self.writes: list[str] = []

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        return self.spec.available_table_fields(configured_fields(self.spec))

    def create_task(self, task: Task) -> MoveResult:
        self.writes.append("create")
        return MoveResult.success(task)

    def update_task(self, task: Task) -> MoveResult:
        self.writes.append("update")
        return MoveResult.success(task)

    def move_task(
        self,
        task: Task,
        target_column: int,
        target_position: int | None = None,
    ) -> MoveResult:
        del target_column, target_position
        self.writes.append("move")
        return MoveResult.success(task)

    def delete_task(self, task_id: int) -> MoveResult:
        self.writes.append("delete")
        task = next(task for task in self._tasks if task.task_id == task_id)
        return MoveResult.success(task)


class TwentyPlusProviderModelEdgeTests(unittest.TestCase):
    def test_all_provider_sorts_keep_27_unique_cards_and_nulls_last(self) -> None:
        self.assertEqual(PROVIDER_NAMES, {spec.name for spec in specs()})
        for spec in specs():
            cards = edge_tasks(spec)
            original = copy.deepcopy(cards)
            available = spec.available_table_fields(configured_fields(spec))
            for column in available:
                sort_key = WORK_ITEM_COLUMN_SPECS[column].sort_key
                if sort_key is None:
                    continue
                with self.subTest(provider=spec.name, column=column.value):
                    for reverse in (False, True):
                        ordered = BoardView(sort=sort_key, reverse=reverse).order(cards)
                        self.assertEqual(EDGE_CARD_COUNT, len(ordered))
                        self.assertEqual(
                            {task.task_id for task in cards},
                            {task.task_id for task in ordered},
                        )
                        missing_started = False
                        for task in ordered:
                            missing = BoardView(sort=sort_key)._sort_value(task) is None
                            missing_started = missing_started or missing
                            if missing_started:
                                self.assertTrue(missing, (spec.name, sort_key, task.task_id))
            self.assertEqual(original, cards, spec.name)

    def test_compound_unicode_filter_is_identical_at_20_21_and_27_cards(self) -> None:
        for spec in specs():
            for count in (20, 21, EDGE_CARD_COUNT):
                with self.subTest(provider=spec.name, count=count):
                    cards = edge_tasks(spec)[:count]
                    cards[0].description = "Straße release note"
                    view = BoardView(card_filter=CardFilter(text="STRASSE"))
                    result = view.apply(cards, finished_ids=set())
                    self.assertEqual([cards[0].task_id], [task.task_id for task in result])


class TwentyPlusProviderTuiEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_providers_render_27_hostile_rows_without_overflow_or_writes(self) -> None:
        for spec in specs():
            with self.subTest(provider=spec.name):
                backend = EdgeSpecBackend(spec)
                app = KanbanApp(backend, confirm_moves=False)
                app.view.columns = list(WorkItemColumn)
                async with app.run_test(size=(80, 24)) as pilot:
                    await pilot.pause()
                    app.set_board_layout(BoardLayout.ROWS)
                    await pilot.pause()
                    view = app.query_one(WorkItemsView)
                    table = view.query_one(DataTable)
                    self.assertEqual(EDGE_CARD_COUNT, table.row_count)
                    self.assertEqual(0, table.max_scroll_x)
                    self.assertGreater(table.max_scroll_y, 0)
                    self.assertTrue(set(view._rendered_columns).issubset(backend.available_task_fields()))

                    rendered = " | ".join(
                        str(table.get_cell_at(Coordinate(row, column)))
                        for row in range(table.row_count)
                        for column in range(len(table.columns))
                    )
                    self.assertFalse(CONTROL_CHARACTERS & set(rendered), repr(rendered))
                    self.assertIn("[bold red]literal markup[/]", rendered)

                    table.move_cursor(row=EDGE_CARD_COUNT - 1)
                    await pilot.pause()
                    selected = view.selected_task()
                    self.assertIsNotNone(selected)
                    assert selected is not None
                    self.assertIn(selected.task_id, {task.task_id for task in backend.get_tasks()})

                    app.view.sort = SortKey.TITLE
                    view.refresh_tasks()
                    await pilot.pause()
                    self.assertEqual(selected.task_id, view.selected_task().task_id)  # type: ignore[union-attr]

                    app.set_board_layout(BoardLayout.SPLIT)
                    await pilot.pause()
                    self.assertEqual(EDGE_CARD_COUNT, table.row_count)
                    self.assertEqual(0, table.max_scroll_x)
                    self.assertEqual(selected.task_id, view.selected_task().task_id)  # type: ignore[union-attr]
                    self.assertEqual([], backend.writes)


if __name__ == "__main__":
    unittest.main()
