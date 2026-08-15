"""Large planning and filter matrices without live provider traffic."""

from __future__ import annotations

import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from pykantui.core.filters import BoardView, CardFilter, FilterState, SortKey, finished_ids
from pykantui.models import BoardLayout, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.filter_fields import FilterFieldName
from pykantui.tracker.registry import specs
from pykantui.tracker.spec import ProviderSpec
from pykantui.tui.app import KanbanApp
from pykantui.workspace import markdown
from pykantui.workspace.disk import OnDisk
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.models import SyncReport
from pykantui.workspace.outbound import apply_plan, create_drafts, pending_drafts
from pykantui.workspace.planner import build_plan
from pykantui.workspace.state import SyncState

from .load_fixtures import (
    PROVIDER_NAMES,
    TASK_BASE_TIME,
    MatrixProvider,
    drafts_for,
    issues_for,
    planning_fixture,
    project_for,
    tasks_for,
)

# Five rounds of 100,000 shared filter evaluations stay below one second in
# the Linux test image. This deliberately generous budget is a regression
# tripwire, not a benchmark competition.
FILTER_BUDGET_SECONDS = 3.0
LOCAL_PROVIDER_FILTERS = {
    FilterFieldName.ASSIGNEE,
    FilterFieldName.ISSUE_TYPE,
    FilterFieldName.PRIORITY,
    FilterFieldName.LABELS,
}
PRIORITY_RANK = {
    "highest": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _expected_sort(tasks: list[Task], sort_key: SortKey) -> list[Task]:
    """Independently order the deterministic fixture for one public sort key."""
    if sort_key is SortKey.MANUAL:
        return list(tasks)

    def value(task: Task) -> str | int | tuple[str, ...] | None:
        metadata = task.metadata
        match sort_key:
            case SortKey.TITLE:
                return task.title.casefold()
            case SortKey.KEY:
                return str(metadata["key"]).casefold()
            case SortKey.STATUS:
                status = str(metadata.get("status") or "")
                return status.casefold() if status else f"\uffff{task.column_id:020d}"
            case SortKey.TYPE:
                return str(metadata.get("issue_type") or "").casefold() or None
            case SortKey.ASSIGNEE:
                return str(metadata.get("assignee") or "").casefold() or None
            case SortKey.REPORTER:
                return str(metadata.get("reporter") or "").casefold() or None
            case SortKey.DUE:
                return task.due_date.toordinal() if task.due_date is not None else None
            case SortKey.CREATED:
                return task.created_at.isoformat()
            case SortKey.AGE:
                return task.days_since_creation
            case SortKey.PRIORITY:
                priority = str(metadata.get("priority") or "").casefold()
                return PRIORITY_RANK.get(priority, len(PRIORITY_RANK)) if priority else None
            case SortKey.LABELS:
                labels = metadata.get("labels")
                if not isinstance(labels, (list, tuple)):
                    return None
                normalized = tuple(str(label).casefold() for label in labels if str(label).strip())
                return normalized or None
            case SortKey.COMPONENTS:
                components = metadata.get("components")
                if not isinstance(components, (list, tuple)):
                    return None
                normalized = tuple(
                    str(component).casefold()
                    for component in components
                    if str(component).strip()
                )
                return normalized or None
    present = [(task, value(task)) for task in tasks]
    ordered = sorted(
        ((task, item) for task, item in present if item is not None),
        key=lambda pair: (pair[1], pair[0].position, pair[0].task_id),
    )
    missing = [task for task, item in present if item is None]
    return [task for task, _item in ordered] + missing


class LargeProviderPlanningMatrixTests(unittest.TestCase):
    def test_all_nine_providers_plan_supported_edits_moves_and_many_creates(self) -> None:
        provider_specs = specs()
        self.assertEqual(PROVIDER_NAMES, {spec.name for spec in provider_specs})

        for spec in provider_specs:
            with self.subTest(provider=spec.name):
                issues = issues_for(spec)
                provider = MatrixProvider(spec, issues)
                on_disk, state = planning_fixture(issues)
                plan = build_plan(provider, project_for(spec), on_disk, state)
                report = SyncReport()

                self.assertTrue(spec.capabilities.create_issues)
                self.assertTrue(spec.capabilities.move_issues)
                self.assertIn("title", provider.editable_card_fields())
                self.assertIn("column_id", provider.editable_card_fields())
                self.assertIn("title", provider.creatable_card_fields())
                self.assertIn("column_id", provider.creatable_card_fields())
                self.assertEqual(300, len(plan.clean()))
                self.assertEqual(300, provider.remote_fetches)
                self.assertEqual(100, sum(item.edit.touched() == ("title",) for item in plan.clean()))
                self.assertEqual(100, sum(item.edit.touched() == ("column_id",) for item in plan.clean()))
                self.assertEqual(
                    100,
                    sum(set(item.edit.touched()) == {"title", "column_id"} for item in plan.clean()),
                )
                self.assertEqual([], report.skipped)

                with TemporaryDirectory() as raw_workspace:
                    workspace = Path(raw_workspace)
                    drafts = drafts_for(spec, workspace)
                    found = pending_drafts(provider, drafts, report)
                    made = create_drafts(
                        workspace,
                        provider,
                        project_for(spec),
                        found,
                        SyncState(),
                        report,
                        ColumnStyle.SLUG,
                    )

                self.assertEqual(100, len(found))
                self.assertEqual(100, len(made))
                self.assertEqual(100, len(provider.creates))
                self.assertTrue(all(draft.due_date == date(2026, 9, 1) for draft in provider.creates))

    def test_runtime_provider_contract_blocks_an_unconfigured_field_before_remote_check(self) -> None:
        monday = next(spec for spec in specs() if spec.name == "monday")
        issue = issues_for(monday)[0]
        provider = MatrixProvider(monday, [issue], config={})
        on_disk = {
            issue.issue_id: OnDisk(
                path=Path("to-do") / issue.filename(),
                column_name="to-do",
                file=markdown.IssueFile(
                    {"id": issue.issue_id, "key": issue.key, "title": issue.title},
                    "An unconfigured Monday description edit",
                    "",
                ),
            )
        }
        plan = build_plan(
            provider,
            project_for(monday),
            on_disk,
            SyncState({issue.issue_id: issue}),
        )

        self.assertEqual([], plan.clean())
        self.assertEqual(1, len(plan.unchecked()))
        self.assertEqual(0, provider.remote_fetches)

        report = SyncReport()
        apply_plan(provider, plan, report, push_conflicts=True)
        self.assertEqual([], provider.updates)
        self.assertEqual([(issue.key, "cannot write body")], report.skipped)


class LargeProviderFilterMatrixTests(unittest.TestCase):
    def test_every_shared_and_provider_filter_has_identical_semantics_in_all_layouts(self) -> None:
        scenario_count = 0
        for spec in specs():
            backend = JsonBackend()
            backend._tasks = tasks_for(spec)
            source = backend.get_tasks()
            app = KanbanApp(backend, confirm_moves=False)
            done = finished_ids(source)
            from_boundary = (TASK_BASE_TIME + timedelta(days=250)).date()
            until_boundary = (TASK_BASE_TIME + timedelta(days=750)).date()

            shared: list[tuple[str, BoardView, list[int]]] = [
                (
                    "compound",
                    BoardView(
                        card_filter=CardFilter(
                            text="release-target",
                            provider={"assignee": "alex"},
                            project=spec.name.swapcase(),
                            column_id=1,
                        ),
                        sort=SortKey.TITLE,
                        reverse=True,
                    ),
                    [
                        task.task_id
                        for task in reversed(sorted(source, key=lambda item: item.title.casefold()))
                        if "release-target" in task.title
                        and task.metadata["assignee"] == "Alex"
                        and str(task.metadata["project"]).casefold() == spec.name.casefold()
                        and task.column_id == 1
                    ],
                ),
                (
                    "text",
                    BoardView(card_filter=CardFilter(text="RELEASE-TARGET")),
                    [task.task_id for task in source if "release-target" in task.title],
                ),
                (
                    "project",
                    BoardView(card_filter=CardFilter(project=spec.name.swapcase())),
                    [
                        task.task_id
                        for task in source
                        if str(task.metadata["project"]).casefold() == spec.name.casefold()
                    ],
                ),
                (
                    "column/status",
                    BoardView(card_filter=CardFilter(column_id=2)),
                    [task.task_id for task in source if task.column_id == 2],
                ),
                (
                    "key",
                    BoardView(card_filter=CardFilter(key="-009")),
                    [task.task_id for task in source if "-009" in str(task.metadata["key"])],
                ),
                (
                    "created_from inclusive",
                    BoardView(card_filter=CardFilter(created_from=from_boundary)),
                    [task.task_id for task in source if task.created_at.date() >= from_boundary],
                ),
                (
                    "created_until inclusive",
                    BoardView(card_filter=CardFilter(created_until=until_boundary)),
                    [task.task_id for task in source if task.created_at.date() <= until_boundary],
                ),
                (
                    "created range inclusive",
                    BoardView(
                        card_filter=CardFilter(
                            created_from=from_boundary,
                            created_until=until_boundary,
                        )
                    ),
                    [
                        task.task_id
                        for task in source
                        if from_boundary <= task.created_at.date() <= until_boundary
                    ],
                ),
                (
                    "reverse",
                    BoardView(reverse=True),
                    [task.task_id for task in reversed(source)],
                ),
            ]
            shared.extend(
                (
                    f"state:{state.value}",
                    BoardView(card_filter=CardFilter(states=[state])),
                    [task.task_id for task in source if _matches_state(task, state, done)],
                )
                for state in FilterState
            )
            sort_expectations = {
                sort_key: _expected_sort(source, sort_key)
                for sort_key in SortKey
            }
            self.assertEqual(set(SortKey), set(sort_expectations))
            shared.extend(
                (
                    f"sort:{sort_key.value}",
                    BoardView(sort=sort_key),
                    [task.task_id for task in ordered],
                )
                for sort_key, ordered in sort_expectations.items()
            )

            available = {
                field.name for field in spec.filter_fields(_configured_fields(spec))
            } & LOCAL_PROVIDER_FILTERS
            wanted = {
                FilterFieldName.ASSIGNEE: "alex",
                FilterFieldName.ISSUE_TYPE: "bug",
                FilterFieldName.PRIORITY: "high",
                FilterFieldName.LABELS: "backend",
            }
            provider_scenarios: list[tuple[str, BoardView, list[int]]] = []
            for field_name in sorted(available, key=lambda field: field.value):
                value = wanted[field_name]
                provider_scenarios.append(
                    (
                        f"provider:{field_name.value}",
                        BoardView(card_filter=CardFilter(provider={field_name.value: value})),
                        [
                            task.task_id
                            for task in source
                            if _metadata_matches(task, field_name.value, value)
                        ],
                    )
                )

            for scenario, view, expected in (*shared, *provider_scenarios):
                with self.subTest(provider=spec.name, scenario=scenario):
                    scenario_count += 1
                    self._assert_all_layouts(app, view, expected)

        # 28 shared cases for nine providers plus 27 provider-field cases.
        # Plane deliberately has no Type capability: its type directory is
        # unavailable to supported accounts, so the UI must not promise it.
        # Every case evaluates 1,000 cards independently in each of 3 layouts.
        self.assertEqual(279, scenario_count)

    def test_repeated_thousand_card_filtering_stays_under_a_conservative_budget(self) -> None:
        source = tasks_for(specs()[0])
        done = finished_ids(source)
        view = BoardView(
            card_filter=CardFilter(
                text="release-target",
                provider={"labels": "backend"},
            ),
            sort=SortKey.PRIORITY,
        )
        durations: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            for _ in range(100):
                result = view.apply(source, finished_ids=done)
                self.assertEqual(50, len(result))
            durations.append(time.perf_counter() - started)

        self.assertLess(max(durations), FILTER_BUDGET_SECONDS, durations)

    def test_jira_query_and_sprint_are_remote_controls_not_local_card_filters(self) -> None:
        jira = next(spec for spec in specs() if spec.name == "jira")
        other_specs = [spec for spec in specs() if spec.name != "jira"]
        remote_controls = {FilterFieldName.QUERY, FilterFieldName.SPRINT}

        self.assertEqual(
            remote_controls,
            {field.name for field in jira.filter_fields()} & remote_controls,
        )
        self.assertTrue(
            all(not ({field.name for field in spec.filter_fields()} & remote_controls) for spec in other_specs)
        )
        self.assertEqual("JQL", jira.capabilities.query_language)
        self.assertNotIn("query", CardFilter.model_fields)
        self.assertNotIn("sprint", CardFilter.model_fields)

        backend = JsonBackend()
        backend._tasks = tasks_for(jira)
        before = [task.task_id for task in backend.get_tasks()]
        backend.set_query_text("project = TEST")
        self.assertFalse(backend.set_sprint_only(True))
        self.assertEqual("", backend.query_text())
        self.assertEqual(before, [task.task_id for task in backend.get_tasks()])

    def _assert_all_layouts(
        self,
        app: KanbanApp,
        view: BoardView,
        expected: list[int],
    ) -> None:
        app.view = view
        by_layout: dict[BoardLayout, list[int]] = {}
        for layout in BoardLayout:
            app.board_layout = layout
            by_layout[layout] = [task.task_id for task in app.visible_tasks()]
        for layout, actual in by_layout.items():
            self.assertEqual(expected, actual, layout.value)


def _configured_fields(spec: ProviderSpec) -> dict[str, object]:
    """Enable optional card/filter columns without provider-specific clients."""
    return {
        field.configuration_key: f"configured-{field.name.value}"
        for field in spec.card_fields
        if field.configuration_key
    }


def _metadata_matches(task: Task, field: str, wanted: str) -> bool:
    value = task.metadata[field]
    if isinstance(value, list):
        return any(str(item).casefold() == wanted.casefold() for item in value)
    return str(value).casefold() == wanted.casefold()


def _matches_state(task: Task, state: FilterState, done: set[int]) -> bool:
    blocked = any(blocker not in done for blocker in task.blocked_by)
    match state:
        case FilterState.BLOCKED:
            return blocked
        case FilterState.UNBLOCKED:
            return not blocked
        case FilterState.OVERDUE:
            return task.due_date is not None and task.due_date < date.today()
        case FilterState.DUE_TODAY:
            return task.due_date == date.today()
        case FilterState.NO_DUE:
            return task.due_date is None
        case FilterState.HAS_NOTES:
            return bool(task.description.strip())


if __name__ == "__main__":
    unittest.main()
