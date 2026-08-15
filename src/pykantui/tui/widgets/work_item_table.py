"""Table primitives and row population for the Rows and Split views."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unicodedata import category

from rich.cells import cell_len
from rich.text import Text
from textual.containers import Horizontal
from textual.coordinate import Coordinate
from textual.events import Click
from textual.geometry import Offset
from textual.message import Message
from textual.widgets import DataTable, Static
from textual.widgets.data_table import CellDoesNotExist

from pykantui.core.work_items import (
    CORE_WORK_ITEM_COLUMNS,
    WORK_ITEM_COLUMN_SPECS,
    WorkItemColumn,
    column_value,
)
from pykantui.i18n import translate as _
from pykantui.models import BoardLayout, Task
from pykantui.tracker.models import ColumnGroup
from pykantui.tui.provider_links import ISSUE_LINK_GLYPH, provider_issue_url
from pykantui.tui.status_styles import WORKFLOW_STATUS_CLASSES, workflow_status_class, workflow_status_text
from pykantui.tui.type_styles import WORK_ITEM_TYPE_CLASSES, work_item_type_class, work_item_type_text
from pykantui.tui.widgets.card_fields import Field
from pykantui.workspace.status import SyncStatus

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class DetailField(Static):
    """One read-only field in the split detail pane."""

    def __init__(self, field: Field) -> None:
        super().__init__(
            "—",
            id=f"work-item-{field.key.replace('_', '-')}",
            classes="work-item-field",
            markup=False,
        )
        self.field = field

    def on_mount(self) -> None:
        self.border_title = _(self.field.title)

    def apply_status_group(self, group: ColumnGroup | str) -> None:
        """Apply the active theme's semantic workflow class to Status."""
        self.remove_class(*WORKFLOW_STATUS_CLASSES)
        self.add_class(workflow_status_class(group))

    def apply_item_type(self, value: object) -> None:
        """Apply the provider-neutral type semantic to the Type field."""
        self.remove_class(*WORK_ITEM_TYPE_CLASSES)
        self.add_class(work_item_type_class(value))


class WorkItemTable(DataTable[object]):
    """Data table that preserves row-level mouse intent for its parent view."""

    link_targets: dict[str, tuple[str, str]]
    _visible_link_key = ""

    class ContextRequested(Message):
        def __init__(self, task_key: str, anchor: Offset) -> None:
            self.task_key = task_key
            self.anchor = anchor
            super().__init__()

    class OpenRequested(Message):
        def __init__(self, task_key: str) -> None:
            self.task_key = task_key
            super().__init__()

    class LinkRequested(Message):
        """A row's visible provider arrow was selected."""

        def __init__(self, task_key: str, provider_id: str, url: str) -> None:
            self.task_key = task_key
            self.provider_id = provider_id
            self.url = url
            super().__init__()

    def on_click(self, event: Click) -> None:
        """Route right/double-click before Textual consumes the table click."""
        metadata = event.style.meta
        row_index = metadata.get("row")
        if not isinstance(row_index, int) or row_index < 0 or not self.is_valid_row_index(row_index):
            return

        row_key = str(self.coordinate_to_cell_key(Coordinate(row_index, 0)).row_key.value or "")

        column_index = metadata.get("column")
        if event.button == 1 and event.chain == 1 and isinstance(column_index, int):
            cell_key = self.coordinate_to_cell_key(Coordinate(row_index, column_index))
            if str(cell_key.column_key.value or "") == WorkItemColumn.KEY.value:
                target = getattr(self, "link_targets", {}).get(row_key)
                if target is not None and row_key == self._visible_link_key:
                    event.stop()
                    self.move_cursor(row=row_index)
                    self.post_message(self.LinkRequested(row_key, *target))
                    return

        if event.button == 3:
            event.stop()
            event.prevent_default()
            self.move_cursor(row=row_index)
            self.post_message(self.ContextRequested(row_key, event.screen_offset))
            return
        if event.button == 1 and event.chain == 2:
            event.stop()
            event.prevent_default()
            self.move_cursor(row=row_index)
            self.post_message(self.OpenRequested(row_key))

    def watch_cursor_coordinate(
        self,
        old_coordinate: Coordinate,
        new_coordinate: Coordinate,
    ) -> None:
        """Move the provider affordance with the selected row."""
        super().watch_cursor_coordinate(old_coordinate, new_coordinate)
        if old_coordinate.row == new_coordinate.row:
            return
        self.show_provider_link(self._row_key_at(new_coordinate.row))

    def show_provider_link(self, row_key: str) -> None:
        """Render one contextual arrow without changing column geometry."""
        previous = self._visible_link_key
        self._visible_link_key = row_key
        if previous and previous != row_key:
            self._render_provider_link(previous, visible=False)
        if row_key:
            self._render_provider_link(row_key, visible=row_key in getattr(self, "link_targets", {}))

    def _row_key_at(self, row_index: int) -> str:
        if not self.is_valid_row_index(row_index):
            return ""
        return str(self.coordinate_to_cell_key(Coordinate(row_index, 0)).row_key.value or "")

    def _render_provider_link(self, row_key: str, *, visible: bool) -> None:
        try:
            value = self.get_cell(row_key, WorkItemColumn.KEY.value)
        except CellDoesNotExist:
            return
        current = value if isinstance(value, Text) else Text(str(value))
        suffix = f" {ISSUE_LINK_GLYPH}"
        label = current.plain.removesuffix(suffix)
        if visible:
            label = f"{label}{suffix}"
        self.update_cell(
            row_key,
            WorkItemColumn.KEY.value,
            Text(
                label,
                style=current.style,
                overflow=current.overflow,
                no_wrap=current.no_wrap,
            ),
            update_width=False,
        )


class WorkItemRowsBase(Horizontal):
    """Shared typed table population for the public work-items view."""

    app: KanbanApp
    _selected_key: str
    _tasks: dict[str, Task]
    list_percent: int
    _rendered_columns: tuple[WorkItemColumn, ...]

    def refresh_tasks(self) -> None:
        """Rebuild rows from the same filtered task list as Kanban."""
        table = self.query_one(WorkItemTable)
        tasks = self.app.visible_tasks()
        self.query_one("#work-items-heading", Static).update(f"{_('Work Items')} ({len(tasks)})")
        previous = self._selected_key
        self._tasks = {str(task.task_id): task for task in tasks}
        table.link_targets = {
            str(task.task_id): (str(task.metadata.get("id", "") or ""), url)
            for task in tasks
            if (url := provider_issue_url(task))
        }

        table.clear(columns=True)
        requested = self.app.view.visible_columns(self.app.backend.available_task_fields())
        self._rendered_columns, widths = self._responsive_columns(requested, table)
        for column in self._rendered_columns:
            table.add_column(
                self._header_label(column),
                key=column.value,
                width=widths[column],
            )

        column_names = {column.column_id: column.name for column in self.app.visible_columns}
        for row_number, task in enumerate(tasks, start=1):
            status_label = column_names.get(task.column_id, str(task.column_id))
            table.add_row(
                *(
                    self._cell(task, column, row_number=row_number, status=status_label)
                    for column in self._rendered_columns
                ),
                key=str(task.task_id),
            )

        selected = previous if previous in self._tasks else next(iter(self._tasks), "")
        self._select(selected)
        if selected:
            selected_row = next(
                (row_index for row_index, task in enumerate(tasks) if str(task.task_id) == selected),
                0,
            )
            table.move_cursor(row=selected_row)
        table.show_provider_link(selected)

    def _responsive_columns(
        self,
        requested: tuple[WorkItemColumn, ...],
        table: DataTable[object],
    ) -> tuple[tuple[WorkItemColumn, ...], dict[WorkItemColumn, int]]:
        """Fit bounded columns without changing the user's saved choices."""
        columns = list(requested)
        available = self._table_width(table)

        def minimum(column: WorkItemColumn) -> int:
            spec = WORK_ITEM_COLUMN_SPECS[column]
            return max(spec.min_width, cell_len(self._header_label(column)))

        def minimum_total() -> int:
            return sum(minimum(column) + 2 * table.cell_padding for column in columns)

        optional = [column for column in reversed(columns) if column not in CORE_WORK_ITEM_COLUMNS]
        people = {WorkItemColumn.ASSIGNEE, WorkItemColumn.REPORTER}
        if self.app.board_layout is BoardLayout.SPLIT:
            # Split is explicitly a people-and-status overview. Preserve its
            # selected Assignee/Reporter fields ahead of the compact sync/#/type
            # gutters, then fall back to the same irreducible identity trio.
            removal_order = [
                *(column for column in optional if column not in people),
                WorkItemColumn.TYPE,
                WorkItemColumn.NUMBER,
                WorkItemColumn.SYNC,
                WorkItemColumn.REPORTER,
                WorkItemColumn.ASSIGNEE,
            ]
        else:
            removal_order = [
                *optional,
                WorkItemColumn.TYPE,
                WorkItemColumn.NUMBER,
                WorkItemColumn.SYNC,
            ]

        # Key, Status and Summary are the irreducible row identity. Removed
        # fields remain selected in BoardView and return after widening.
        for column in removal_order:
            if minimum_total() <= available:
                break
            if column in columns:
                columns.remove(column)

        widths = {column: minimum(column) for column in columns}
        excess = minimum_total() - available
        if excess > 0 and WorkItemColumn.SUMMARY in widths:
            widths[WorkItemColumn.SUMMARY] = max(1, widths[WorkItemColumn.SUMMARY] - excess)

        used = sum(width + 2 * table.cell_padding for width in widths.values())
        remaining = max(0, available - used)
        grow_order = [
            WorkItemColumn.SUMMARY,
            WorkItemColumn.KEY,
            WorkItemColumn.STATUS,
            *(column for column in columns if column not in {
                WorkItemColumn.SUMMARY,
                WorkItemColumn.KEY,
                WorkItemColumn.STATUS,
            }),
        ]
        for column in grow_order:
            if column not in widths or remaining <= 0:
                continue
            wanted = WORK_ITEM_COLUMN_SPECS[column].preferred_width
            growth = min(remaining, max(0, wanted - widths[column]))
            widths[column] += growth
            remaining -= growth
        return tuple(columns), widths

    def _table_width(self, table: DataTable[object]) -> int:
        """Estimate the laid-out table cells before rows trigger a scrollbar."""
        if self.app.board_layout is BoardLayout.SPLIT:
            workspace_width = max(1, self.app.size.width - 3)
            width = int(workspace_width * self.list_percent / 100) - 2
        else:
            width = self.app.size.width - 4
        return max(1, width - table.styles.scrollbar_size_vertical)

    def _header_label(self, column: WorkItemColumn) -> str:
        spec = WORK_ITEM_COLUMN_SPECS[column]
        label = _(spec.label)
        if spec.sort_key is self.app.view.sort:
            return f"{label} {'↓' if self.app.view.reverse else '↑'}"
        return label

    def _cell(
        self,
        task: Task,
        column: WorkItemColumn,
        *,
        row_number: int,
        status: str,
    ) -> Text:
        """Build a one-line, ellipsized Rich cell for one normalized value."""
        if column is WorkItemColumn.SYNC:
            return self._status_text(self._status(task))
        if column is WorkItemColumn.STATUS:
            label = _one_line_cell(status or "—")
            return workflow_status_text(
                label,
                self.app.backend.column_group(task.column_id),
                self.app.theme_variables,
            )
        if column is WorkItemColumn.KEY:
            label = _one_line_cell(
                column_value(task, column, row_number=row_number, status=status) or "—"
            )
            return Text(label, overflow="ellipsis", no_wrap=True)
        value = column_value(task, column, row_number=row_number, status=status)
        if column is WorkItemColumn.TYPE:
            label = _one_line_cell(value) if value not in (None, "") else "—"
            return work_item_type_text(label, value, self.app.theme_variables)
        label = _one_line_cell(value) if value not in (None, "") else "—"
        return Text(label, overflow="ellipsis", no_wrap=True)

    @staticmethod
    def _status(task: Task) -> SyncStatus | None:
        try:
            return SyncStatus(str(task.metadata.get("sync_status", "")))
        except ValueError:
            return None

    def _status_text(self, status: SyncStatus | None) -> Text:
        """Render a Rich table cell from the active Textual theme safely."""
        if status is None:
            return Text("—")
        variable = status.colour.removeprefix("$")
        colour = self.app.theme_variables.get(variable, "") if variable else ""
        return Text(f"{status.marker} {status.label}", style=colour)

    def selected_task(self) -> Task | None:
        """Return the card selected in the work-item table, if any."""
        return self._tasks.get(self._selected_key)

    def _select(self, key: str) -> None:
        """Render the selected row; implemented by the concrete view."""
        raise NotImplementedError


def _one_line_cell(value: object) -> str:
    """Make provider-controlled table text safe without interpreting markup."""
    output: list[str] = []
    for character in str(value):
        if character in {"\r", "\n", "\t"}:
            output.append(" ")
        elif category(character) not in {"Cc", "Zl", "Zp"}:
            output.append(character)
        else:
            # Unicode line/paragraph separators are not terminal controls,
            # but they still violate DataTable's one-row-per-card contract.
            if category(character) in {"Zl", "Zp"}:
                output.append(" ")
    return "".join(output)
