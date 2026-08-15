"""The board's domain objects.

These stay backend-agnostic on purpose. Anything a specific backend needs to
round-trip (a Jira issue key, an assignee) goes in ``Task.metadata`` rather than
becoming a field here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pykantui.sync.base import Backend


# Default column ids. They live here rather than in core/workflows.py so that
# Board's defaults can use them without an import cycle; workflows builds the
# columns and the Jira status map from these same numbers.
TODO_COLUMN = 1
IN_PROGRESS_COLUMN = 2
NEEDS_REVIEW_COLUMN = 3
WAITING_COLUMN = 4
DONE_COLUMN = 5
ARCHIVE_COLUMN = 6


class Category(BaseModel):
    category_id: int
    name: str
    color: str = "#3b3b58"


class Column(BaseModel):
    column_id: int
    name: str
    position: int = 0

    #: Hidden columns are not on the board at all.
    visible: bool = True

    #: Collapsed columns are still on the board and still valid move targets;
    #: they just render as a narrow strip showing the name and a count.
    collapsed: bool = False


class Board(BaseModel):
    board_id: int
    name: str
    created_at: datetime = Field(default_factory=datetime.now)

    # Landing in one of these columns is what drives a task's timestamps. Any
    # other column — Waiting, for instance — leaves them alone, so a paused task
    # keeps the start date it already had and is not finished.
    reset_column: int = TODO_COLUMN
    start_column: int = IN_PROGRESS_COLUMN
    finish_column: int = DONE_COLUMN


class Task(BaseModel):
    task_id: int
    title: str
    column_id: int
    board_id: int = 1
    position: int = 0
    description: str = ""
    category_id: int | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    due_date: date | None = None
    blocked_by: list[int] = Field(default_factory=list)
    blocking: list[int] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    @property
    def days_since_creation(self) -> int:
        return (datetime.now().date() - self.created_at.date()).days

    @property
    def days_left(self) -> int | None:
        if self.due_date is None:
            return None
        return (self.due_date - datetime.now().date()).days

    def apply_column_transition(self, new_column: int, board: Board) -> None:
        """Set ``started_at``/``finished_at`` from the column the task lands in.

        Landing in the reset column clears both, so a card dragged back to the
        left genuinely restarts rather than keeping a stale completion date.
        """
        now = datetime.now()
        if new_column == board.reset_column:
            self.started_at = None
            self.finished_at = None
        elif new_column == board.start_column:
            self.started_at = self.started_at or now
            self.finished_at = None
        elif new_column == board.finish_column:
            self.started_at = self.started_at or now
            self.finished_at = now
        self.column_id = new_column

    def can_move_to(self, target_column: int, board: Board, backend: Backend) -> tuple[bool, str]:
        """Return ``(allowed, reason)`` for moving this task into a column.

        Only the start and finish columns are gated. Shuffling a blocked card
        around the backlog is fine; claiming it is started or done is not.
        """
        if target_column not in (board.start_column, board.finish_column):
            return True, ""
        if not self.blocked_by:
            return True, ""

        blockers = [task for task in backend.get_tasks_by_ids(self.blocked_by) if not task.finished]
        if not blockers:
            return True, ""

        names = ", ".join(task.title.splitlines()[0] for task in blockers[:3])
        suffix = ", ..." if len(blockers) > 3 else ""
        return False, f"Blocked by {len(blockers)} unfinished task(s): {names}{suffix}"


def same_task_identity(left: Task, right: Task) -> bool:
    """Return whether two UI snapshots still identify the same card.

    Local boards have stable numeric ids. Provider boards additionally carry
    the provider's immutable issue id in metadata because their numeric row id
    is rebuilt whenever Markdown files are reloaded.
    """
    if left.task_id != right.task_id:
        return False
    left_provider_id = str(left.metadata.get("id", "") or "")
    right_provider_id = str(right.metadata.get("id", "") or "")
    if left_provider_id or right_provider_id:
        return left_provider_id == right_provider_id
    return True


class MoveResult(BaseModel):
    """Outcome of a backend write.

    The board never mutates a card until it has one of these back with
    ``ok=True``, so a rejected write leaves the screen untouched.
    """

    ok: bool
    message: str = ""
    task: Task | None = None

    @classmethod
    def success(cls, task: Task, message: str = "") -> MoveResult:
        return cls(ok=True, message=message, task=task)

    @classmethod
    def failure(cls, message: str) -> MoveResult:
        return cls(ok=False, message=message, task=None)
