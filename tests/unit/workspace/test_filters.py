"""Filtering and sorting, at the model level."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from pykantui.core.filters import BoardView, CardFilter, FilterState, SortKey, finished_ids
from pykantui.models import Task


def task(
    task_id: int,
    title: str = "card",
    *,
    due_in: int | None = None,
    description: str = "",
    blocked_by: list[int] | None = None,
    finished: bool = False,
    age: int = 0,
    metadata: dict[str, object] | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        title=title,
        column_id=1,
        description=description,
        due_date=date.today() + timedelta(days=due_in) if due_in is not None else None,
        blocked_by=blocked_by or [],
        finished_at=datetime.now() if finished else None,
        created_at=datetime.now() - timedelta(days=age),
        metadata=metadata or {},
    )


class TextSearchTests(unittest.TestCase):
    def test_matches_the_title(self) -> None:
        card = CardFilter(text="postgres")

        self.assertTrue(card.matches(task(1, "Upgrade Postgres to 16"), blocked=False))
        self.assertFalse(card.matches(task(2, "Rate-limit the API"), blocked=False))

    def test_matches_the_description(self) -> None:
        card = CardFilter(text="runbook")

        self.assertTrue(card.matches(task(1, "Deploy", description="see the runbook"), blocked=False))

    def test_is_case_insensitive(self) -> None:
        card = CardFilter(text="POSTGRES")

        self.assertTrue(card.matches(task(1, "upgrade postgres"), blocked=False))

    def test_an_empty_filter_matches_everything(self) -> None:
        card = CardFilter()

        self.assertTrue(card.matches(task(1, "anything"), blocked=False))
        self.assertFalse(card.active)


class StateTests(unittest.TestCase):
    def test_blocked_and_unblocked_are_opposites(self) -> None:
        blocked = CardFilter(states=[FilterState.BLOCKED])
        unblocked = CardFilter(states=[FilterState.UNBLOCKED])
        card = task(1, blocked_by=[9])

        self.assertTrue(blocked.matches(card, blocked=True))
        self.assertFalse(unblocked.matches(card, blocked=True))

    def test_overdue(self) -> None:
        card = CardFilter(states=[FilterState.OVERDUE])

        self.assertTrue(card.matches(task(1, due_in=-2), blocked=False))
        self.assertFalse(card.matches(task(2, due_in=0), blocked=False))
        self.assertFalse(card.matches(task(3), blocked=False))

    def test_due_today(self) -> None:
        card = CardFilter(states=[FilterState.DUE_TODAY])

        self.assertTrue(card.matches(task(1, due_in=0), blocked=False))
        self.assertFalse(card.matches(task(2, due_in=1), blocked=False))

    def test_no_due_date(self) -> None:
        card = CardFilter(states=[FilterState.NO_DUE])

        self.assertTrue(card.matches(task(1), blocked=False))
        self.assertFalse(card.matches(task(2, due_in=3), blocked=False))

    def test_has_notes_ignores_whitespace(self) -> None:
        card = CardFilter(states=[FilterState.HAS_NOTES])

        self.assertTrue(card.matches(task(1, description="something"), blocked=False))
        self.assertFalse(card.matches(task(2, description="   "), blocked=False))

    def test_states_are_cumulative_not_alternative(self) -> None:
        card = CardFilter(states=[FilterState.OVERDUE, FilterState.HAS_NOTES])

        self.assertTrue(card.matches(task(1, due_in=-1, description="x"), blocked=False))
        self.assertFalse(card.matches(task(2, due_in=-1), blocked=False))
        self.assertFalse(card.matches(task(3, description="x"), blocked=False))

    def test_toggle_adds_then_removes(self) -> None:
        card = CardFilter()

        card.toggle_state(FilterState.OVERDUE)
        self.assertEqual(card.states, [FilterState.OVERDUE])

        card.toggle_state(FilterState.OVERDUE)
        self.assertEqual(card.states, [])


class ProviderFieldTests(unittest.TestCase):
    def test_matching_a_scalar_field(self) -> None:
        card = CardFilter(provider={"assignee": "Alex"})

        self.assertTrue(card.matches(task(1, metadata={"assignee": "Alex"}), blocked=False))
        self.assertFalse(card.matches(task(2, metadata={"assignee": "Sam"}), blocked=False))

    def test_matching_is_case_insensitive(self) -> None:
        card = CardFilter(provider={"priority": "HIGH"})

        self.assertTrue(card.matches(task(1, metadata={"priority": "High"}), blocked=False))

    def test_a_list_field_matches_any_entry(self) -> None:
        card = CardFilter(provider={"labels": "backend"})

        self.assertTrue(card.matches(task(1, metadata={"labels": ["ui", "backend"]}), blocked=False))
        self.assertFalse(card.matches(task(2, metadata={"labels": ["ui"]}), blocked=False))

    def test_a_missing_field_does_not_match(self) -> None:
        card = CardFilter(provider={"assignee": "Alex"})

        self.assertFalse(card.matches(task(1), blocked=False))

    def test_old_saved_jira_filter_key_migrates_to_provider(self) -> None:
        card = CardFilter.model_validate({"jira": {"assignee": "Alex"}})

        self.assertEqual({"assignee": "Alex"}, card.provider)
        self.assertIn("provider", card.model_dump())
        self.assertNotIn("jira", card.model_dump())


class ProviderScopeTests(unittest.TestCase):
    def test_scope_uses_explicit_provider_project_metadata(self) -> None:
        card = CardFilter(project="octo/repo")

        self.assertTrue(
            card.matches(
                task(1, metadata={"key": "repo#123", "project": "octo/repo"}),
                blocked=False,
            )
        )


class BlockedTests(unittest.TestCase):
    def test_a_card_is_blocked_while_its_blocker_is_unfinished(self) -> None:
        blocker = task(1)
        blocked = task(2, blocked_by=[1])
        view = BoardView(card_filter=CardFilter(states=[FilterState.BLOCKED]))

        kept = view.apply([blocker, blocked], finished_ids=finished_ids([blocker, blocked]))

        self.assertEqual([card.task_id for card in kept], [2])

    def test_finishing_the_blocker_unblocks_it(self) -> None:
        blocker = task(1, finished=True)
        blocked = task(2, blocked_by=[1])
        view = BoardView(card_filter=CardFilter(states=[FilterState.BLOCKED]))

        kept = view.apply([blocker, blocked], finished_ids=finished_ids([blocker, blocked]))

        self.assertEqual(kept, [])


class SortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cards = [
            task(1, "Charlie", due_in=5, age=1, metadata={"priority": "Low"}),
            task(2, "alpha", due_in=None, age=10, metadata={"priority": "Highest"}),
            task(3, "Bravo", due_in=-1, age=3, metadata={"priority": "Medium"}),
        ]

    def order(self, view: BoardView) -> list[int]:
        return [card.task_id for card in view.order(self.cards)]

    def test_manual_leaves_the_order_alone(self) -> None:
        self.assertEqual(self.order(BoardView()), [1, 2, 3])

    def test_title_is_case_insensitive(self) -> None:
        self.assertEqual(self.order(BoardView(sort=SortKey.TITLE)), [2, 3, 1])

    def test_due_soonest_first_and_undated_last(self) -> None:
        self.assertEqual(self.order(BoardView(sort=SortKey.DUE)), [3, 1, 2])

    def test_age_newest_first(self) -> None:
        self.assertEqual(self.order(BoardView(sort=SortKey.AGE)), [1, 3, 2])

    def test_priority_most_urgent_first(self) -> None:
        self.assertEqual(self.order(BoardView(sort=SortKey.PRIORITY)), [2, 3, 1])

    def test_reverse_flips_whatever_is_showing(self) -> None:
        self.assertEqual(self.order(BoardView(sort=SortKey.TITLE, reverse=True)), [1, 3, 2])

    def test_reverse_works_on_manual_too(self) -> None:
        self.assertEqual(self.order(BoardView(reverse=True)), [3, 2, 1])

    def test_sorting_does_not_mutate_the_input(self) -> None:
        BoardView(sort=SortKey.TITLE).order(self.cards)

        self.assertEqual([card.task_id for card in self.cards], [1, 2, 3])

    def test_an_unknown_priority_sorts_last(self) -> None:
        cards = [task(1, metadata={"priority": "Weird"}), task(2, metadata={"priority": "High"})]

        ordered = BoardView(sort=SortKey.PRIORITY).order(cards)
        self.assertEqual([card.task_id for card in ordered], [2, 1])


class SummaryTests(unittest.TestCase):
    def test_nothing_active_reads_as_inactive(self) -> None:
        view = BoardView()

        self.assertFalse(view.active)
        self.assertEqual(view.summary(), "")

    def test_the_summary_names_what_is_on(self) -> None:
        view = BoardView(
            card_filter=CardFilter(text="pay", states=[FilterState.OVERDUE]),
            sort=SortKey.DUE,
            reverse=True,
        )

        summary = view.summary()
        self.assertIn('"pay"', summary)
        self.assertIn("overdue", summary)
        self.assertIn("by due", summary)
        self.assertIn("reversed", summary)

    def test_a_sort_alone_counts_as_active(self) -> None:
        self.assertTrue(BoardView(sort=SortKey.TITLE).active)

    def test_clear_resets_the_filter(self) -> None:
        card = CardFilter(text="x", states=[FilterState.OVERDUE], provider={"assignee": "Alex"})

        card.clear()

        self.assertFalse(card.active)


if __name__ == "__main__":
    unittest.main()
