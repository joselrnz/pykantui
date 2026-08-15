"""What the board is showing: a filter, and a sort order.

Both are presentation. Filtering hides cards; sorting reorders them on screen.
Neither writes anything, so the order you arranged by hand survives underneath
and comes back the moment you pick Manual again.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, Field, field_validator

from pykantui.core.sorting import SORT_LABELS, SortKey
from pykantui.core.work_items import (
    CORE_WORK_ITEM_COLUMNS,
    DEFAULT_WORK_ITEM_COLUMNS,
    WORK_ITEM_COLUMN_SPECS,
    WorkItemColumn,
)
from pykantui.models import Task


class FilterState(StrEnum):
    """A property of a card you can filter on."""

    BLOCKED = "blocked"
    UNBLOCKED = "unblocked"
    OVERDUE = "overdue"
    DUE_TODAY = "due_today"
    NO_DUE = "no_due"
    HAS_NOTES = "has_notes"


#: What each state is called in the menu.
STATE_LABELS = {
    FilterState.BLOCKED: "Blocked",
    FilterState.UNBLOCKED: "Unblocked",
    FilterState.OVERDUE: "Overdue",
    FilterState.DUE_TODAY: "Due today",
    FilterState.NO_DUE: "No due date",
    FilterState.HAS_NOTES: "Has notes",
}


#: Jira priorities, most urgent first. Anything unrecognised sorts last.
PRIORITY_ORDER = ["highest", "blocker", "critical", "high", "medium", "normal", "low", "lowest", "trivial"]


class CardFilter(BaseModel):
    """Cumulative: every condition set has to hold, not any of them."""

    text: str = ""
    states: list[FilterState] = Field(default_factory=list)

    #: Provider metadata key -> required value, matched case-insensitively.
    #: ``jira`` is accepted as a migration alias for existing saved filters.
    provider: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("provider", "jira"),
    )

    #: Jira project key, taken from the issue key prefix (SCRUM-25 -> SCRUM).
    project: str = ""

    #: A single column, the board's equivalent of a Jira status.
    column_id: int | None = None

    #: Substring of the issue key, or of the card id on a local board.
    key: str = ""

    created_from: date | None = None
    created_until: date | None = None

    @property
    def active(self) -> bool:
        return bool(
            self.text
            or self.states
            or self.provider
            or self.project
            or self.column_id is not None
            or self.key
            or self.created_from
            or self.created_until
        )

    def summary(self) -> str:
        """A short description for the bar, so a live filter is never hidden."""
        parts = [STATE_LABELS[state].lower() for state in self.states]
        parts += [f"{key}={value}" for key, value in self.provider.items()]
        if self.project:
            parts.append(f"project={self.project}")
        if self.key:
            parts.append(f"key~{self.key}")
        if self.created_from:
            parts.append(f"from {self.created_from.isoformat()}")
        if self.created_until:
            parts.append(f"to {self.created_until.isoformat()}")
        if self.text:
            parts.insert(0, f'"{self.text}"')
        return ", ".join(parts)

    def toggle_state(self, state: FilterState) -> None:
        if state in self.states:
            self.states.remove(state)
        else:
            self.states.append(state)

    def clear(self) -> None:
        self.text = ""
        self.states.clear()
        self.provider.clear()
        self.project = ""
        self.column_id = None
        self.key = ""
        self.created_from = None
        self.created_until = None

    def matches(self, task: Task, *, blocked: bool) -> bool:
        if self.text and not self._matches_text(task):
            return False
        if self.column_id is not None and task.column_id != self.column_id:
            return False
        if self.project and project_of(task).casefold() != self.project.casefold():
            return False
        if self.key and self.key.casefold() not in card_key(task).casefold():
            return False
        created = task.created_at.date()
        if self.created_from and created < self.created_from:
            return False
        if self.created_until and created > self.created_until:
            return False
        for state in self.states:
            if not self._matches_state(task, state, blocked=blocked):
                return False
        return all(self._matches_provider(task, key, value) for key, value in self.provider.items())

    def _matches_text(self, task: Task) -> bool:
        needle = self.text.casefold()
        return needle in task.title.casefold() or needle in task.description.casefold()

    @staticmethod
    def _matches_state(task: Task, state: FilterState, *, blocked: bool) -> bool:
        days = task.days_left
        match state:
            case FilterState.BLOCKED:
                return blocked
            case FilterState.UNBLOCKED:
                return not blocked
            case FilterState.OVERDUE:
                return days is not None and days < 0
            case FilterState.DUE_TODAY:
                return days == 0
            case FilterState.NO_DUE:
                return task.due_date is None
            case FilterState.HAS_NOTES:
                return bool(task.description.strip())

    @staticmethod
    def _matches_provider(task: Task, key: str, wanted: str) -> bool:
        value = task.metadata.get(key)
        if isinstance(value, list):
            return any(str(item).casefold() == wanted.casefold() for item in value)
        return str(value or "").casefold() == wanted.casefold()


class BoardView(BaseModel):
    """The filter and sort currently applied to the board."""

    card_filter: CardFilter = Field(default_factory=CardFilter)
    sort: SortKey = SortKey.MANUAL
    reverse: bool = False
    columns: list[WorkItemColumn] = Field(default_factory=lambda: list(DEFAULT_WORK_ITEM_COLUMNS))

    @field_validator("columns", mode="before")
    @classmethod
    def _normalise_columns(cls, raw: object) -> list[WorkItemColumn]:
        """Preserve valid optional choices while always restoring core fields."""
        choices: list[WorkItemColumn] = list(CORE_WORK_ITEM_COLUMNS)
        if not isinstance(raw, (list, tuple)):
            return list(DEFAULT_WORK_ITEM_COLUMNS)
        for value in raw:
            try:
                column = WorkItemColumn(value)
            except (TypeError, ValueError):
                continue
            if column not in choices:
                choices.append(column)
        return choices

    @property
    def sorted(self) -> bool:
        return self.sort is not SortKey.MANUAL

    @property
    def active(self) -> bool:
        return self.card_filter.active or self.sorted or self.reverse

    def summary(self) -> str:
        parts = []
        if self.card_filter.active:
            parts.append(self.card_filter.summary())
        if self.sorted:
            parts.append(f"by {SORT_LABELS[self.sort].lower()}")
        if self.reverse:
            parts.append("reversed")
        return " · ".join(parts)

    def apply(self, tasks: list[Task], *, finished_ids: set[int]) -> list[Task]:
        """Filter then sort.

        ``finished_ids`` is passed in rather than looked up per card: deciding
        whether a card is blocked needs its blockers' state, and asking the
        backend once per card is an N+1 against Jira.
        """
        kept = [task for task in tasks if self.card_filter.matches(task, blocked=self._is_blocked(task, finished_ids))]
        return self.order(kept)

    def order(self, tasks: list[Task]) -> list[Task]:
        """Sort a column's cards. Stable, so Manual is left exactly as it was."""
        if self.sort is SortKey.MANUAL:
            return list(reversed(tasks)) if self.reverse else tasks

        present: list[tuple[Task, str | int | tuple[str, ...]]] = []
        missing: list[Task] = []
        for task in tasks:
            value = self._sort_value(task)
            if value is None:
                missing.append(task)
            else:
                present.append((task, value))
        present.sort(
            key=lambda item: (item[1], item[0].position, item[0].task_id),
            reverse=self.reverse,
        )
        # Nulls are always last. Reversing must not promote an unassigned or
        # undated card above actual values.
        return [task for task, _ in present] + missing

    def _sort_value(self, task: Task) -> str | int | tuple[str, ...] | None:
        metadata = task.metadata
        match self.sort:
            case SortKey.TITLE:
                lines = task.title.splitlines()
                return (lines[-1] if lines else "").casefold()
            case SortKey.KEY:
                return card_key(task).casefold()
            case SortKey.STATUS:
                status = str(metadata.get("status") or "")
                return status.casefold() if status else f"\uffff{task.column_id:020d}"
            case SortKey.TYPE:
                return _text_sort_value(metadata.get("issue_type"))
            case SortKey.ASSIGNEE:
                return _text_sort_value(metadata.get("assignee"))
            case SortKey.REPORTER:
                return _text_sort_value(metadata.get("reporter"))
            case SortKey.DUE:
                return task.due_date.toordinal() if task.due_date is not None else None
            case SortKey.CREATED:
                return task.created_at.isoformat()
            case SortKey.AGE:
                # Newest first, so the smallest age sorts first. Negating this
                # would quietly give oldest-first, which is not what it says.
                return task.days_since_creation
            case SortKey.PRIORITY:
                name = str(metadata.get("priority", "")).casefold()
                if not name:
                    return None
                rank = PRIORITY_ORDER.index(name) if name in PRIORITY_ORDER else len(PRIORITY_ORDER)
                return rank
            case SortKey.LABELS:
                labels = metadata.get("labels")
                if isinstance(labels, (list, tuple)):
                    normalized = tuple(str(label).casefold() for label in labels if str(label).strip())
                    return normalized or None
                return _text_sort_value(labels)
            case SortKey.COMPONENTS:
                components = metadata.get("components")
                if isinstance(components, (list, tuple)):
                    normalized = tuple(
                        str(component).casefold()
                        for component in components
                        if str(component).strip()
                    )
                    return normalized or None
                return _text_sort_value(components)
        return task.position

    def visible_columns(
        self,
        available: Collection[WorkItemColumn],
    ) -> tuple[WorkItemColumn, ...]:
        """Selected columns that the current provider actually supplies."""
        supported = set(available) | set(CORE_WORK_ITEM_COLUMNS)
        return tuple(column for column in WorkItemColumn if column in self.columns and column in supported)

    def toggle_column(
        self,
        column: WorkItemColumn,
        *,
        available: Collection[WorkItemColumn],
    ) -> bool:
        """Toggle an optional available column; required columns stay visible."""
        if column in CORE_WORK_ITEM_COLUMNS or column not in set(available):
            return False
        if column in self.columns:
            self.columns.remove(column)
        else:
            self.columns.append(column)
        return True

    def set_column_sort(self, column: WorkItemColumn) -> bool:
        """Select a sortable header; selecting it again flips its direction."""
        sort_key = WORK_ITEM_COLUMN_SPECS[column].sort_key
        if sort_key is None:
            return False
        if self.sort is sort_key:
            self.reverse = not self.reverse
        else:
            self.sort = sort_key
            self.reverse = False
        return True

    @staticmethod
    def _is_blocked(task: Task, finished_ids: set[int]) -> bool:
        return any(blocker not in finished_ids for blocker in task.blocked_by)


def project_of(task: Task) -> str:
    """The provider scope a card belongs to.

    Provider workspaces store it explicitly because a GitHub ``repo#12`` or a
    Trello card id has no Jira-style key prefix. Older/local data still falls
    back to the historical ``ABC-123`` convention.
    """
    explicit = str(task.metadata.get("project") or "")
    if explicit:
        return explicit
    key = str(task.metadata.get("jira_key") or task.metadata.get("key") or "")
    return key.split("-")[0] if "-" in key else ""


def card_key(task: Task) -> str:
    """What the user would type to find this card: its issue key, or its id."""
    return str(task.metadata.get("jira_key") or task.metadata.get("key") or task.task_id)


def finished_ids(tasks: list[Task]) -> set[int]:
    return {task.task_id for task in tasks if task.finished}


def is_overdue(task: Task) -> bool:
    return task.due_date is not None and task.due_date < date.today()


def _text_sort_value(value: object) -> str | None:
    text = str(value or "").strip()
    return text.casefold() if text else None


__all__ = [
    "SORT_LABELS",
    "BoardView",
    "CardFilter",
    "FilterState",
    "SortKey",
    "card_key",
    "finished_ids",
    "is_overdue",
    "project_of",
]
