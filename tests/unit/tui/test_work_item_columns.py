"""Provider-aware work-item columns and header sorting."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from typing import Any

from pykantui.core.actions import Action, ActionKind, ColumnCommand, Menu
from pykantui.core.filters import BoardView, SortKey
from pykantui.core.work_items import (
    CORE_WORK_ITEM_COLUMNS,
    DEFAULT_WORK_ITEM_COLUMNS,
    WORK_ITEM_COLUMN_SPECS,
    WorkItemColumn,
    available_work_item_columns,
    column_value,
)
from pykantui.models import BoardLayout, MovementMode, Task
from pykantui.pages.menu import MenuItem
from pykantui.providers.monday import MondayProvider
from pykantui.providers.trello import TrelloProvider
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker import specs
from pykantui.tui.menu_items import build_menu_items


def task(
    task_id: int,
    *,
    title: str = "",
    column_id: int = 1,
    created_at: datetime | None = None,
    due_date: date | None = None,
    metadata: dict[str, Any] | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        title=title or f"Task {task_id}",
        column_id=column_id,
        created_at=created_at or datetime(2026, 1, task_id),
        due_date=due_date,
        metadata=metadata or {},
    )


class WorkItemColumnContractTests(unittest.TestCase):
    def test_every_column_has_one_complete_spec(self) -> None:
        self.assertEqual(set(WorkItemColumn), set(WORK_ITEM_COLUMN_SPECS))
        for column, spec in WORK_ITEM_COLUMN_SPECS.items():
            with self.subTest(column=column):
                self.assertEqual(column, spec.column)
                self.assertTrue(spec.label)
                self.assertGreaterEqual(spec.preferred_width, spec.min_width)
                self.assertEqual(spec.sort_key is not None, spec.sortable)

    def test_identity_columns_are_required_and_type_is_optional(self) -> None:
        self.assertEqual(
            (
                WorkItemColumn.SYNC,
                WorkItemColumn.NUMBER,
                WorkItemColumn.KEY,
                WorkItemColumn.STATUS,
                WorkItemColumn.SUMMARY,
            ),
            CORE_WORK_ITEM_COLUMNS,
        )
        self.assertTrue(all(WORK_ITEM_COLUMN_SPECS[column].required for column in CORE_WORK_ITEM_COLUMNS))
        self.assertFalse(WORK_ITEM_COLUMN_SPECS[WorkItemColumn.TYPE].required)

    def test_column_values_use_normalised_task_data(self) -> None:
        card = task(
            7,
            title="Header\nA concise summary",
            due_date=date(2026, 2, 3),
            metadata={
                "sync_status": "edited",
                "key": "JPT-7",
                "status": "In Progress",
                "issue_type": "Story",
                "assignee": "Alex",
                "reporter": "Sam",
                "priority": "High",
                "labels": ["api", "urgent"],
                "components": ["API", "Auth"],
            },
        )

        self.assertEqual("edited", column_value(card, WorkItemColumn.SYNC, row_number=4))
        self.assertEqual(4, column_value(card, WorkItemColumn.NUMBER, row_number=4))
        self.assertEqual("JPT-7", column_value(card, WorkItemColumn.KEY))
        self.assertEqual("In Progress", column_value(card, WorkItemColumn.STATUS))
        self.assertEqual("Story", column_value(card, WorkItemColumn.TYPE))
        self.assertEqual("A concise summary", column_value(card, WorkItemColumn.SUMMARY))
        self.assertEqual("Alex", column_value(card, WorkItemColumn.ASSIGNEE))
        self.assertEqual("Sam", column_value(card, WorkItemColumn.REPORTER))
        self.assertEqual("High", column_value(card, WorkItemColumn.PRIORITY))
        self.assertEqual("2026-02-03", column_value(card, WorkItemColumn.DUE))
        self.assertEqual("api, urgent", column_value(card, WorkItemColumn.LABELS))
        self.assertEqual("API, Auth", column_value(card, WorkItemColumn.COMPONENTS))
        self.assertEqual("2026-01-07", column_value(card, WorkItemColumn.CREATED))


class ProviderAvailabilityTests(unittest.TestCase):
    def test_all_builtin_providers_publish_the_exact_optional_column_contract(self) -> None:
        expected = {
            "asana": {"assignee", "created", "due", "reporter"},
            "clickup": {"assignee", "created", "due", "labels", "priority", "reporter", "type"},
            "forgejo": {"assignee", "created", "due", "labels", "reporter"},
            "github": {"assignee", "created", "labels", "reporter", "type"},
            "jira": {
                "assignee",
                "components",
                "created",
                "due",
                "labels",
                "priority",
                "reporter",
                "type",
            },
            "linear": {"assignee", "created", "due", "labels", "priority", "reporter"},
            "monday": {"created", "reporter"},
            "plane": {"assignee", "created", "due", "labels", "priority", "reporter"},
            "shortcut": {"assignee", "created", "due", "labels", "reporter", "type"},
            "trello": {"assignee", "due", "labels", "reporter"},
        }

        actual = {
            provider.name: {
                column.value
                for column in provider.available_table_fields({})
                if column not in CORE_WORK_ITEM_COLUMNS
            }
            for provider in specs()
        }

        self.assertEqual(expected, actual)

    def test_local_backend_declares_columns_without_inspecting_rows(self) -> None:
        backend = JsonBackend()

        self.assertIn(WorkItemColumn.ASSIGNEE, backend.available_task_fields())
        self.assertIn(WorkItemColumn.CREATED, backend.available_task_fields())

    def test_monday_optional_fields_require_configured_board_columns(self) -> None:
        plain = MondayProvider.spec.available_table_fields({})
        configured = MondayProvider.spec.available_table_fields(
            {
                "assignee_column": "people",
                "priority_column": "priority",
                "labels_column": "labels",
                "due_column": "due",
            }
        )

        self.assertNotIn(WorkItemColumn.ASSIGNEE, plain)
        self.assertNotIn(WorkItemColumn.PRIORITY, plain)
        self.assertIn(WorkItemColumn.CREATED, plain)
        self.assertTrue(
            {
                WorkItemColumn.ASSIGNEE,
                WorkItemColumn.PRIORITY,
                WorkItemColumn.LABELS,
                WorkItemColumn.DUE,
            }.issubset(configured)
        )

    def test_read_only_reporter_is_explicit_not_inferred_from_editability(self) -> None:
        github = __import__("pykantui.providers.github", fromlist=["GitHubProvider"]).GitHubProvider

        self.assertIn(WorkItemColumn.REPORTER, github.spec.available_table_fields({}))
        self.assertNotIn("reporter", github.spec.editable_card_fields({}))
        self.assertIn(WorkItemColumn.REPORTER, TrelloProvider.spec.available_table_fields({}))

    def test_available_columns_are_derived_from_provider_fields(self) -> None:
        available = available_work_item_columns(TrelloProvider.spec.card_fields, {})

        self.assertTrue(set(CORE_WORK_ITEM_COLUMNS).issubset(available))
        self.assertIn(WorkItemColumn.ASSIGNEE, available)
        self.assertIn(WorkItemColumn.LABELS, available)
        self.assertIn(WorkItemColumn.DUE, available)
        self.assertNotIn(WorkItemColumn.PRIORITY, available)

    def test_jira_exposes_components_as_an_optional_table_field(self) -> None:
        jira = __import__("pykantui.providers.jira", fromlist=["JiraProvider"]).JiraProvider

        self.assertIn(WorkItemColumn.COMPONENTS, jira.spec.available_table_fields({}))


class BoardViewColumnTests(unittest.TestCase):
    def test_old_serialized_view_gets_the_required_defaults(self) -> None:
        view = BoardView.model_validate({"sort": "title", "reverse": True})

        self.assertEqual(list(DEFAULT_WORK_ITEM_COLUMNS), view.columns)

    def test_serialization_uses_enum_values(self) -> None:
        view = BoardView(columns=[*CORE_WORK_ITEM_COLUMNS, WorkItemColumn.ASSIGNEE])

        dumped = view.model_dump(mode="json")
        self.assertIn("assignee", dumped["columns"])
        self.assertEqual(view, BoardView.model_validate(dumped))

    def test_invalid_duplicates_and_missing_required_columns_are_normalised(self) -> None:
        view = BoardView.model_validate({"columns": ["assignee", "assignee", "not-real"]})

        self.assertEqual(
            [*CORE_WORK_ITEM_COLUMNS, WorkItemColumn.ASSIGNEE],
            view.columns,
        )

    def test_toggle_only_changes_available_optional_columns(self) -> None:
        available = frozenset({*CORE_WORK_ITEM_COLUMNS, WorkItemColumn.ASSIGNEE})
        view = BoardView()

        self.assertFalse(view.toggle_column(WorkItemColumn.SUMMARY, available=available))
        self.assertFalse(view.toggle_column(WorkItemColumn.REPORTER, available=available))
        self.assertTrue(view.toggle_column(WorkItemColumn.ASSIGNEE, available=available))
        self.assertIn(WorkItemColumn.ASSIGNEE, view.visible_columns(available))
        self.assertTrue(view.toggle_column(WorkItemColumn.ASSIGNEE, available=available))
        self.assertNotIn(WorkItemColumn.ASSIGNEE, view.visible_columns(available))

    def test_unavailable_saved_columns_stay_selected_but_are_not_rendered(self) -> None:
        view = BoardView(columns=[*CORE_WORK_ITEM_COLUMNS, WorkItemColumn.REPORTER])

        self.assertNotIn(WorkItemColumn.REPORTER, view.visible_columns(CORE_WORK_ITEM_COLUMNS))
        self.assertIn(WorkItemColumn.REPORTER, view.columns)


class HeaderSortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cards = [
            task(
                1,
                title="Zulu",
                due_date=date(2026, 5, 3),
                metadata={"key": "P-9", "status": "Todo", "issue_type": "Task", "assignee": "Zoe"},
            ),
            task(
                2,
                title="alpha",
                due_date=None,
                metadata={"key": "P-2", "status": "Done", "issue_type": "Bug", "assignee": ""},
            ),
            task(
                3,
                title="Bravo",
                due_date=date(2026, 5, 1),
                metadata={"key": "P-10", "status": "Doing", "issue_type": "Story", "assignee": "Alex"},
            ),
        ]

    def ids(self, view: BoardView) -> list[int]:
        return [card.task_id for card in view.order(self.cards)]

    def test_each_sortable_header_selects_ascending_then_descending(self) -> None:
        for column, spec in WORK_ITEM_COLUMN_SPECS.items():
            if not spec.sortable:
                continue
            with self.subTest(column=column):
                view = BoardView()
                self.assertTrue(view.set_column_sort(column))
                self.assertIs(view.sort, spec.sort_key)
                self.assertFalse(view.reverse)
                self.assertTrue(view.set_column_sort(column))
                self.assertTrue(view.reverse)

    def test_sync_and_row_number_are_not_sortable(self) -> None:
        view = BoardView(sort=SortKey.TITLE)

        self.assertFalse(view.set_column_sort(WorkItemColumn.SYNC))
        self.assertFalse(view.set_column_sort(WorkItemColumn.NUMBER))
        self.assertIs(view.sort, SortKey.TITLE)

    def test_null_values_stay_last_in_both_directions(self) -> None:
        ascending = BoardView(sort=SortKey.DUE)
        descending = BoardView(sort=SortKey.DUE, reverse=True)

        self.assertEqual([3, 1, 2], self.ids(ascending))
        self.assertEqual([1, 3, 2], self.ids(descending))

    def test_assignee_nulls_stay_last_in_both_directions(self) -> None:
        self.assertEqual([3, 1, 2], self.ids(BoardView(sort=SortKey.ASSIGNEE)))
        self.assertEqual([1, 3, 2], self.ids(BoardView(sort=SortKey.ASSIGNEE, reverse=True)))

    def test_component_collection_sort_is_deterministic_and_null_last(self) -> None:
        cards = [
            task(1, metadata={"components": ["Zeta"]}),
            task(2, metadata={"components": []}),
            task(3, metadata={"components": ["API", "Auth"]}),
        ]

        ascending = BoardView(sort=SortKey.COMPONENTS).order(cards)
        descending = BoardView(sort=SortKey.COMPONENTS, reverse=True).order(cards)

        self.assertEqual([3, 1, 2], [card.task_id for card in ascending])
        self.assertEqual([1, 3, 2], [card.task_id for card in descending])

    def test_status_sort_handles_mixed_provider_and_local_cards(self) -> None:
        cards = [
            task(1, metadata={"status": "Done"}),
            task(2, column_id=3),
            task(3, metadata={"status": "Doing"}),
        ]

        ordered = BoardView(sort=SortKey.STATUS).order(cards)

        self.assertEqual([3, 1, 2], [card.task_id for card in ordered])

    def test_summary_sort_ignores_provider_key_title_prefix(self) -> None:
        cards = [
            task(1, title="ZZZ-1\nAlpha"),
            task(2, title="AAA-2\nBravo"),
        ]

        ordered = BoardView(sort=SortKey.TITLE).order(cards)

        self.assertEqual([1, 2], [card.task_id for card in ordered])


class ColumnsMenuTests(unittest.TestCase):
    @staticmethod
    def menu(
        layout: BoardLayout,
        view: BoardView,
        available: frozenset[WorkItemColumn],
    ) -> list[MenuItem]:
        return build_menu_items(
            Menu.COLUMNS,
            view=view,
            board_layout=layout,
            movement_mode=MovementMode.ADJACENT,
            confirm_moves=False,
            supports_sync=True,
            provider_fields=(),
            available_columns=available,
            saved_filters={},
            filter_prefix="filter-",
        )

    def test_kanban_keeps_board_column_commands(self) -> None:
        items = self.menu(BoardLayout.KANBAN, BoardView(), frozenset(WorkItemColumn))

        actions = [Action.parse(item.key) for item in items]
        self.assertTrue(all(action and action.kind is ActionKind.COL for action in actions))
        self.assertEqual(
            {command.value for command in ColumnCommand if command.needs_config or command is ColumnCommand.EXPAND_ALL},
            {action.value for action in actions if action},
        )

    def test_rows_and_split_offer_only_available_optional_fields(self) -> None:
        available = frozenset(
            {*CORE_WORK_ITEM_COLUMNS, WorkItemColumn.ASSIGNEE, WorkItemColumn.DUE}
        )
        for layout in (BoardLayout.ROWS, BoardLayout.SPLIT):
            with self.subTest(layout=layout):
                items = self.menu(layout, BoardView(), available)
                actions = [Action.parse(item.key) for item in items]
                self.assertEqual(
                    [WorkItemColumn.ASSIGNEE.value, WorkItemColumn.DUE.value],
                    [action.value for action in actions if action],
                )
                self.assertTrue(
                    all(action and action.kind is ActionKind.TABLE_COLUMN for action in actions)
                )

    def test_table_column_action_round_trips_through_the_wire(self) -> None:
        action = Action.of(ActionKind.TABLE_COLUMN, WorkItemColumn.ASSIGNEE)

        parsed = Action.parse(action.encode())

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertIs(WorkItemColumn.ASSIGNEE, parsed.enum(WorkItemColumn))


if __name__ == "__main__":
    unittest.main()
