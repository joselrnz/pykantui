"""Split-pane sizing and coalesced terminal-resize behavior."""

from __future__ import annotations

from collections.abc import Collection

from textual.containers import Horizontal, Vertical
from textual.events import MouseDown, MouseMove, MouseUp, Resize
from textual.timer import Timer
from textual.widgets import Static

from pykantui.core.work_items import WorkItemColumn
from pykantui.models import BoardLayout

_SIDEBAR_DETAIL_COLUMNS = frozenset(
    {
        WorkItemColumn.TYPE,
        WorkItemColumn.ASSIGNEE,
        WorkItemColumn.REPORTER,
        WorkItemColumn.PRIORITY,
        WorkItemColumn.DUE,
        WorkItemColumn.LABELS,
        WorkItemColumn.COMPONENTS,
        WorkItemColumn.CREATED,
    }
)
_CORE_SIDEBAR_FIELD_COUNT = 3  # Summary, Status, Key.
_EDITOR_ONLY_FIELDS = frozenset({"body"})


class WorkItemResizeMixin(Horizontal):
    """Keep Rows/Split fitting the terminal without rebuilding per pixel."""

    DEFAULT_LIST_PERCENT = 35
    STANDARD_LIST_PERCENT = 40
    COMPACT_LIST_PERCENT = 45
    DENSE_SIDEBAR_FIELD_COUNT = 11
    STANDARD_SIDEBAR_FIELD_COUNT = 9
    MIN_LIST_PERCENT = 25
    MAX_LIST_PERCENT = 75
    MIN_DETAIL_PANE_WIDTH = 39
    RESIZE_STEP = 5
    TABLE_RESIZE_DELAY = 0.075

    list_percent: int
    default_list_percent: int
    _split_width_initialized: bool
    _dragging_resizer: bool
    _resize_timer: Timer | None
    _resize_pending: bool

    @property
    def editor_active(self) -> bool:
        """Whether a draft must survive layout changes."""
        raise NotImplementedError

    def refresh_tasks(self) -> None:
        """Rebuild the concrete table after its final width is known."""
        raise NotImplementedError

    def set_layout(self, layout: BoardLayout) -> None:
        split = layout is BoardLayout.SPLIT
        self.query_one("#work-item-detail-pane", Vertical).display = split
        self.set_class(split, "split")
        self.set_class(layout is BoardLayout.ROWS, "rows")
        self._apply_split_width()

    @classmethod
    def _default_percent_for_field_density(
        cls,
        available_fields: Collection[WorkItemColumn],
        editable_fields: Collection[str],
        *,
        provider_backed: bool,
    ) -> int:
        """Choose a provider-neutral initial ratio from declared field density."""
        if not provider_backed:
            return cls.COMPACT_LIST_PERCENT
        sidebar_fields = (
            _CORE_SIDEBAR_FIELD_COUNT
            + len(set(available_fields) & _SIDEBAR_DETAIL_COLUMNS)
            + len(set(editable_fields) & _EDITOR_ONLY_FIELDS)
        )
        if sidebar_fields >= cls.DENSE_SIDEBAR_FIELD_COUNT:
            return cls.DEFAULT_LIST_PERCENT
        if sidebar_fields >= cls.STANDARD_SIDEBAR_FIELD_COUNT:
            return cls.STANDARD_LIST_PERCENT
        return cls.COMPACT_LIST_PERCENT

    def _initialize_split_width(
        self,
        available_fields: Collection[WorkItemColumn],
        editable_fields: Collection[str],
        *,
        provider_backed: bool,
    ) -> None:
        """Set the capability-derived default once, before user adjustments."""
        if self._split_width_initialized:
            return
        default = self._default_percent_for_field_density(
            available_fields,
            editable_fields,
            provider_backed=provider_backed,
        )
        self.default_list_percent = default
        self.list_percent = default
        self._split_width_initialized = True

    def _set_list_percent(self, percent: int) -> None:
        self.list_percent = min(self.MAX_LIST_PERCENT, max(self.MIN_LIST_PERCENT, percent))
        self._apply_split_width()
        # Layout settles on the next frame; reuse the same restartable timer as
        # terminal resizing so held keys and mouse drags never rebuild per step.
        if self.editor_active:
            self._resize_pending = True
        else:
            self._schedule_table_refit()

    def _apply_split_width(self) -> None:
        """Size both panes while reserving usable editor space when narrow."""
        if not self.query("#work-items-list-pane"):
            return
        effective_percent = self.list_percent
        divider_width = self.query_one("#work-item-resizer", Static).size.width
        available = self.content_region.width - divider_width
        if available > 0:
            safe_maximum = (available - self.MIN_DETAIL_PANE_WIDTH) * 100 // available
            effective_percent = min(
                effective_percent,
                max(self.MIN_LIST_PERCENT, safe_maximum),
            )
        self.query_one("#work-items-list-pane", Vertical).styles.width = f"{effective_percent}fr"
        self.query_one("#work-item-detail-pane", Vertical).styles.width = f"{100 - effective_percent}fr"

    def action_shrink_list(self) -> None:
        self._set_list_percent(self.list_percent - self.RESIZE_STEP)

    def action_grow_list(self) -> None:
        self._set_list_percent(self.list_percent + self.RESIZE_STEP)

    def action_reset_split(self) -> None:
        self._set_list_percent(self.default_list_percent)

    def on_resize(self, _event: Resize) -> None:
        """Re-fit the table after a terminal-resize burst, not for every pixel."""
        if not self.display:
            return
        self._apply_split_width()
        if self.editor_active:
            self._resize_pending = True
            return
        self._schedule_table_refit()

    def _schedule_table_refit(self) -> None:
        """Restart the one short timer shared by terminal and divider resize."""
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(self.TABLE_RESIZE_DELAY, self._finish_resize)

    def _finish_resize(self) -> None:
        self._resize_timer = None
        if self.display and not self.editor_active:
            self._resize_pending = False
            self.refresh_tasks()
        else:
            self._resize_pending = True

    def _refresh_after_editor(self) -> None:
        """Apply one deferred resize after preserving/removing editor widgets."""
        pending = self._resize_pending or self._resize_timer is not None
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None
        self._resize_pending = False
        if pending:
            self.call_after_refresh(self.refresh_tasks)

    def on_mouse_down(self, event: MouseDown) -> None:
        """Start resizing only when the left button lands on the center line."""
        handle = self.query_one("#work-item-resizer", Static)
        if event.button != 1 or not handle.region.contains_point(event.screen_offset):
            return
        event.stop()
        self._dragging_resizer = True
        handle.add_class("-dragging")
        self.capture_mouse()

    def on_mouse_move(self, event: MouseMove) -> None:
        if not self._dragging_resizer:
            return
        event.stop()
        handle_width = self.query_one("#work-item-resizer", Static).size.width
        available = max(1, self.content_region.width - handle_width)
        left = event.screen_x - self.content_region.x
        self._set_list_percent(round(left * 100 / available))

    def on_mouse_up(self, event: MouseUp) -> None:
        if not self._dragging_resizer:
            return
        event.stop()
        self._dragging_resizer = False
        self.query_one("#work-item-resizer", Static).remove_class("-dragging")
        self.release_mouse()
