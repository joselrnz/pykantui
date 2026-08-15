"""The backend contract.

Everything the TUI needs from a task store is on this class. Keeping it this
small is what lets a Jira board and a local JSON file drive the same widgets.

Every capability that only some stores have is a method with a default here,
not an attribute the UI is expected to go looking for. The app asks the backend
questions; it never rummages through its attributes to guess what it can do.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pykantui.config import BoardConfig
from pykantui.core.work_items import LOCAL_WORK_ITEM_COLUMNS, WorkItemColumn
from pykantui.models import BackendKind, Board, Category, Column, ColumnRole, MoveResult, Task
from pykantui.tracker.columns import group_from_name
from pykantui.tracker.filter_fields import FilterFieldSpec
from pykantui.tracker.models import ColumnGroup, CommentDraft, RemoteComment

LOCAL_EDITABLE_FIELDS = frozenset(
    {
        "title",
        "body",
        "column_id",
        "assignee",
        "labels",
        "due_date",
        "priority",
        "issue_type",
    }
)


class Backend(ABC):
    kind: ClassVar[BackendKind]

    def display_kind(self) -> str:
        """What to call this store when telling the user something about it.

        ``kind`` is a fixed two-value enum, which is fine for deciding *how* a
        backend behaves but wrong for naming it: a Trello board behind
        :class:`~pykantui.sync.provider.ProviderBackend` would otherwise be
        described as "jira" in "this writes to jira". Anything that reaches a
        human should ask this instead.
        """
        return str(self.kind)

    #: Whether cards can be created, edited and deleted through the TUI.
    writable: ClassVar[bool] = True

    #: Whether the store has a client-side row order. Jira does not, so the
    #: ``J``/``K`` reorder bindings are hidden rather than silently no-op.
    supports_reorder: ClassVar[bool] = True

    #: Whether the board is the result of a query that can be re-run and
    #: re-written — a JQL box and a sprint toggle only make sense if it is.
    supports_query: ClassVar[bool] = False

    #: Whether cards carry tracker fields worth filtering on — assignee, issue
    #: type, key. Asked rather than inferred from ``kind``: every tracker has
    #: these, not only Jira, and the filter bar should offer them for all of
    #: them.
    supports_issue_fields: ClassVar[bool] = False

    #: Whether this board has a provider workspace that can be reconciled.
    supports_sync: ClassVar[bool] = False

    # ---- queries -------------------------------------------------------

    @abstractmethod
    def get_boards(self) -> list[Board]: ...

    @abstractmethod
    def get_active_board(self) -> Board: ...

    @abstractmethod
    def get_columns(self) -> list[Column]: ...

    @abstractmethod
    def get_tasks(self) -> list[Task]: ...

    def get_categories(self) -> list[Category]:
        return []

    def warnings(self) -> list[str]:
        """Configuration problems worth telling the user about on startup."""
        return []

    def board_config(self) -> BoardConfig | None:
        """The editable column shape, where this backend has one.

        ``None`` means the columns cannot be edited from inside the app — the
        UI hides those actions rather than offering them and failing.
        """
        return None

    def reload_config(self) -> None:
        """Re-read the board shape from disk.

        Called when the user refreshes, so a `kbn columns` change lands in every
        open board without restarting it. Overridden where the shape can change.
        """
        return None

    def reload_local(self) -> None:
        """Reload files owned by this backend without contacting a provider."""
        self.reload_config()

    def can_create_tasks(self) -> bool:
        return self.writable

    def can_edit_tasks(self) -> bool:
        return self.writable

    def can_edit_task(self, task: Task) -> bool:
        """Whether this particular visible card may be edited.

        Most stores apply one board-wide capability. Provider backends may
        also show context cards that are deliberately read-only.
        """
        del task
        return self.can_edit_tasks()

    def can_delete_tasks(self) -> bool:
        return self.writable

    def can_delete_task(self, task: Task) -> bool:
        """Whether this exact visible task may be deleted."""
        del task
        return self.can_delete_tasks()

    def delete_requires_confirmation(self, task: Task) -> bool:
        """Whether the TUI must confirm before deleting this task."""
        del task
        return False

    def editable_task_fields(self) -> frozenset[str]:
        """Provider-model fields the TUI may persist for a card.

        Local backends own the whole card. Provider backends override this
        with the exact fields their API adapter declares writable.
        """
        if not self.can_edit_tasks():
            return frozenset()
        return LOCAL_EDITABLE_FIELDS

    def creatable_task_fields(self) -> frozenset[str]:
        """Fields accepted while creating a card; distinct from edit support."""
        if not self.can_create_tasks():
            return frozenset()
        return LOCAL_EDITABLE_FIELDS

    def supports_private_notes(self) -> bool:
        """Whether cards have a local-only Markdown notes section."""
        return False

    def can_read_task_comments(self, task: Task) -> bool:
        """Whether this card has a locally available provider discussion."""

        del task
        return False

    def can_add_task_comment(self, task: Task) -> bool:
        """Whether a local comment draft may be attached to this card."""

        del task
        return False

    def get_task_comments(self, task: Task) -> tuple[RemoteComment | CommentDraft, ...]:
        """Return the locally cached thread; this base implementation is inert."""

        del task
        return ()

    def save_comment_draft(self, task: Task, body: str) -> MoveResult:
        """Persist a local comment draft without crossing a provider boundary."""

        del task, body
        return MoveResult.failure(f"{self.display_kind()} backend has no provider comments")

    def refresh_task_comments(self, task: Task) -> MoveResult:
        """Refresh one provider discussion without sending local card changes."""

        del task
        return MoveResult.failure(f"{self.display_kind()} backend has no provider comments")

    def provider_filter_fields(self) -> tuple[FilterFieldSpec, ...]:
        """Provider-specific boxes for the expanded filter bar.

        Local boards return none. Provider workspaces override this with their
        typed provider contract, keeping tracker terminology out of the TUI.
        """

        return ()

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        """Rows/Split columns available without inspecting current card data."""
        return LOCAL_WORK_ITEM_COLUMNS

    def get_task_by_id(self, task_id: int) -> Task | None:
        return next((task for task in self.get_tasks() if task.task_id == task_id), None)

    def get_tasks_by_ids(self, task_ids: list[int]) -> list[Task]:
        """Fetch several tasks at once.

        One call, not one call per id — the board asks for a card's blockers on
        every render, so a per-id loop here turns into an N+1 against Jira.
        """
        if not task_ids:
            return []
        wanted = set(task_ids)
        return [task for task in self.get_tasks() if task.task_id in wanted]

    def get_visible_columns(self) -> list[Column]:
        return sorted((column for column in self.get_columns() if column.visible), key=lambda c: c.position)

    def column_group(self, column_id: int) -> ColumnGroup:
        """Return the provider-neutral workflow meaning of one column.

        Local boards may name columns freely, so explicit timestamp roles are
        the strongest evidence. A conflicting role assignment is treated as
        unknown rather than choosing one silently. Columns without roles use
        the same conservative name classifier as provider adapters.
        """
        column = next((item for item in self.get_columns() if item.column_id == column_id), None)
        if column is None:
            return ColumnGroup.UNKNOWN

        config = self.board_config()
        if config is not None:
            role_groups = {
                group
                for role, group in (
                    (ColumnRole.RESET, ColumnGroup.TODO),
                    (ColumnRole.START, ColumnGroup.STARTED),
                    (ColumnRole.FINISH, ColumnGroup.DONE),
                )
                if config.column_for(role) == column_id
            }
            if len(role_groups) == 1:
                return role_groups.pop()
            if role_groups:
                return ColumnGroup.UNKNOWN

        return ColumnGroup(group_from_name(column.name))

    def set_column_collapsed(self, column_id: int, collapsed: bool) -> None:
        """Remember a column's collapsed state.

        Presentation rather than data, so a backend that cannot store it just
        lets the state live for the session. Overridden where it can persist.
        """
        del column_id, collapsed

    # ---- the query behind the board ------------------------------------
    #
    # A local board has none of this, so the defaults are all inert and the
    # filter bar disables the matching fields rather than hiding them.

    def query_text(self) -> str:
        """The query the board's cards came from, in the store's own language."""
        return ""

    def can_run_query(self) -> bool:
        """Whether this exact backend exposes an executable remote query."""
        return self.supports_query

    def set_query_text(self, query: str) -> None:
        """Change the query. Takes effect on the next fetch, not immediately."""
        del query

    def run_query(self, query: str) -> None:
        """Apply and execute a provider query without sending card changes.

        Local stores inherit the inert implementation. Provider workspaces
        override it with their explicit read-only pull path.
        """
        self.set_query_text(query)
        self.invalidate()

    def sprint_only(self) -> bool:
        """Whether the board is currently showing just a sprint."""
        return False

    def set_sprint_only(self, active: bool) -> bool:
        """Switch between the sprint and the query.

        Returns whether the backend could do it, so the caller can say why
        nothing happened rather than leaving a toggle that silently does not.
        """
        del active
        return False

    def invalidate(self) -> None:
        """Drop anything cached, so the next read goes back to the source."""
        return None

    # ---- writes --------------------------------------------------------

    @abstractmethod
    def move_task(self, task: Task, target_column: int, target_position: int | None = None) -> MoveResult:
        """Move a task into ``target_column``.

        Called *before* the widget tree changes. Return a failed ``MoveResult``
        rather than raising when the store refuses the move — a Jira workflow
        with no matching transition is an ordinary outcome, not an error.
        """

    def reorder_task(self, task: Task, target_position: int) -> MoveResult:
        if not self.supports_reorder:
            return MoveResult.failure(f"{self.display_kind()} backend has no row order")
        raise NotImplementedError

    def create_task(self, task: Task) -> MoveResult:
        return MoveResult.failure(f"{self.display_kind()} backend is read-only")

    def update_task(self, task: Task) -> MoveResult:
        return MoveResult.failure(f"{self.display_kind()} backend is read-only")

    def delete_task(self, task_id: int) -> MoveResult:
        return MoveResult.failure(f"{self.display_kind()} backend is read-only")

    def delete_task_if_current(self, task: Task) -> MoveResult:
        """Delete a selected immutable snapshot when the backend allows it."""
        if not self.can_delete_task(task):
            return MoveResult.failure(f"{self.display_kind()} backend cannot delete that card")
        return self.delete_task(task.task_id)

    def next_task_id(self) -> int:
        tasks = self.get_tasks()
        return max((task.task_id for task in tasks), default=0) + 1
