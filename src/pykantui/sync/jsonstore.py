"""A local JSON-file backend.

The default store, and the one the tests drive. Writes the whole document on
every mutation — fine for a board that fits on a screen, and it keeps the file
readable and hand-editable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pykantui.config import BoardConfig, default_config, write_text_atomic
from pykantui.models import BackendKind, Board, Category, Column, MoveResult, Task
from pykantui.sync.base import Backend


class JsonBackend(Backend):
    kind: ClassVar[BackendKind] = BackendKind.JSON
    writable: ClassVar[bool] = True
    supports_reorder: ClassVar[bool] = True

    def __init__(self, path: Path | None = None, config: BoardConfig | None = None) -> None:
        self.path = path

        # Columns come from the saved board shape, not from this file, so one
        # config drives every board and the Jira backend alike.
        self.config = config if config is not None else default_config()
        self._categories: list[Category] = []
        self._tasks: list[Task] = []
        if self.path is not None and self.path.exists():
            self.load()

    @property
    def _board(self) -> Board:
        first = self.config.first_column_id() or 0
        return Board(
            board_id=1,
            name="Board",
            reset_column=self.config.reset_column if self.config.reset_column is not None else first,
            start_column=self.config.start_column if self.config.start_column is not None else first,
            finish_column=self.config.finish_column if self.config.finish_column is not None else first,
        )

    def board_config(self) -> BoardConfig:
        return self.config

    def reload_config(self) -> None:
        """Pick up column changes made by ``kbn columns`` while this was open.

        Only for a board whose config came from a file. A demo or test board
        holds its shape in memory, and re-reading would swap in the user's real
        columns underneath it.
        """
        if self.config.path is None:
            return
        self.config = BoardConfig.load(self.config.path)

    # ---- persistence ---------------------------------------------------

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        document: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        self._categories = [Category(**item) for item in document.get("categories", [])]
        self._tasks = [Task(**item) for item in document.get("tasks", [])]

    def save(self) -> None:
        if self.path is None:
            return
        document = {
            "categories": [category.model_dump(mode="json") for category in self._categories],
            "tasks": [task.model_dump(mode="json") for task in self._tasks],
        }
        write_text_atomic(self.path, json.dumps(document, indent=2))

    # ---- queries -------------------------------------------------------

    def get_boards(self) -> list[Board]:
        return [self._board]

    def get_active_board(self) -> Board:
        return self._board

    def get_columns(self) -> list[Column]:
        return self.config.to_columns()

    def get_categories(self) -> list[Category]:
        return list(self._categories)

    def get_tasks(self) -> list[Task]:
        return sorted(self._tasks, key=lambda task: (task.column_id, task.position))

    def tasks_in_column(self, column_id: int) -> list[Task]:
        return [task for task in self.get_tasks() if task.column_id == column_id]

    def set_column_collapsed(self, column_id: int, collapsed: bool) -> None:
        column = self.config.find(column_id)
        if column is not None:
            column.collapsed = collapsed
            self.config.save()

    # ---- writes --------------------------------------------------------

    def move_task(self, task: Task, target_column: int, target_position: int | None = None) -> MoveResult:
        stored = self._find(task.task_id)
        if stored is None:
            return MoveResult.failure(f"No task with id {task.task_id}")
        if self.config.find(target_column) is None:
            return MoveResult.failure(f"No column with id {target_column}")

        origin = stored.column_id
        stored.apply_column_transition(target_column, self._board)

        siblings = [t for t in self.tasks_in_column(target_column) if t.task_id != stored.task_id]
        index = len(siblings) if target_position is None else max(0, min(target_position, len(siblings)))
        siblings.insert(index, stored)
        self._renumber(siblings)
        if origin != target_column:
            self._renumber(self.tasks_in_column(origin))

        self.save()
        return MoveResult.success(stored.model_copy(deep=True))

    def reorder_task(self, task: Task, target_position: int) -> MoveResult:
        stored = self._find(task.task_id)
        if stored is None:
            return MoveResult.failure(f"No task with id {task.task_id}")

        siblings = [t for t in self.tasks_in_column(stored.column_id) if t.task_id != stored.task_id]
        index = max(0, min(target_position, len(siblings)))
        siblings.insert(index, stored)
        self._renumber(siblings)

        self.save()
        return MoveResult.success(stored.model_copy(deep=True))

    def create_task(self, task: Task) -> MoveResult:
        if any(existing.task_id == task.task_id for existing in self._tasks):
            return MoveResult.failure(f"Task id {task.task_id} already exists")
        task.position = len(self.tasks_in_column(task.column_id))
        self._tasks.append(task)
        self.save()
        return MoveResult.success(task.model_copy(deep=True))

    def update_task(self, task: Task) -> MoveResult:
        stored = self._find(task.task_id)
        if stored is None:
            return MoveResult.failure(f"No task with id {task.task_id}")
        self._tasks[self._tasks.index(stored)] = task
        self.save()
        return MoveResult.success(task.model_copy(deep=True))

    def delete_task(self, task_id: int) -> MoveResult:
        stored = self._find(task_id)
        if stored is None:
            return MoveResult.failure(f"No task with id {task_id}")
        self._tasks.remove(stored)
        for other in self._tasks:
            if task_id in other.blocked_by:
                other.blocked_by.remove(task_id)
            if task_id in other.blocking:
                other.blocking.remove(task_id)
        self._renumber(self.tasks_in_column(stored.column_id))
        self.save()
        return MoveResult.success(stored)

    # ---- helpers -------------------------------------------------------

    def _find(self, task_id: int) -> Task | None:
        return next((task for task in self._tasks if task.task_id == task_id), None)

    @staticmethod
    def _renumber(tasks: list[Task]) -> None:
        for position, task in enumerate(tasks):
            task.position = position


def demo_backend(path: Path | None = None) -> JsonBackend:
    """A board with enough shape to exercise every binding."""
    backend = JsonBackend(path=path)
    backend._tasks = [
        Task(task_id=1, title="Wire up the Jira backend", column_id=1, position=0, description="First task"),
        Task(task_id=2, title="Add a settings screen", column_id=1, position=1),
        Task(task_id=3, title="Ship 0.1.0", column_id=1, position=2, blocked_by=[1]),
        Task(task_id=4, title="Read the reference clone", column_id=2, position=0),
        Task(task_id=5, title="Confirm the status names", column_id=3, position=0),
        Task(task_id=6, title="Waiting on the Jira token", column_id=4, position=0),
        Task(task_id=7, title="Pick a name", column_id=5, position=0),
    ]
    backend._tasks[0].blocking = [3]
    backend.save()
    return backend
