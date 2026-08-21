"""Capture genuine Split editor layouts from Textual's compositor.

The harness is deterministic and offline. It asserts terminal-cell geometry
before saving SVGs and records those measurements in ``metrics.json``. PNGs
are optional rasterizations of the exact SVG capture, never recreated mockups.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, TabbedContent, TextArea

from pykantui.core.work_items import WorkItemColumn
from pykantui.models import BoardLayout, Task
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.edit import TaskEditScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.filter_fields import FilterFieldSpec
from pykantui.tracker.registry import get
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.tui.widgets.work_item_table import WorkItemTable
from pykantui.tui.widgets.work_items import WorkItemsView

NORMAL_SIZE = (160, 46)
SHORT_SIZE = (96, 18)
NARROW_SIZE = (80, 24)
FILTER_SIZE = (120, 18)
POPUP_NORMAL_SIZE = (150, 40)
OUTPUT_NAMES = {
    "normal": "split-editor-normal-160x46",
    "short_info": "split-editor-short-info-private-notes-96x18",
    "short_details": "split-editor-short-details-components-96x18",
    "filters": "split-expanded-jira-filters-120x18",
    "popup_normal": "detail-popup-description-private-150x40",
    "popup_compact": "detail-popup-private-notes-96x18",
    "create_normal": "new-card-popup-notes-150x40",
    "create_compact": "new-card-popup-notes-96x18",
}

MIN_WRITING_HEIGHT = 8


class ScreenshotBackend(JsonBackend):
    """Local deterministic backend exposing every normalized provider field."""

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        return frozenset(WorkItemColumn)

    def editable_task_fields(self) -> frozenset[str]:
        return super().editable_task_fields() | {"components"}

    def supports_private_notes(self) -> bool:
        return True


class OfflineJiraBackend(ScreenshotBackend):
    """In-memory board using Jira's real typed field/filter contract."""

    supports_issue_fields = True
    supports_query = True
    supports_reorder = False
    supports_sync = True

    def display_kind(self) -> str:
        return "Jira"

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        return get("jira").spec.available_table_fields({})

    def editable_task_fields(self) -> frozenset[str]:
        return frozenset(get("jira").spec.editable_card_fields({}))

    def provider_filter_fields(self) -> tuple[FilterFieldSpec, ...]:
        return get("jira").spec.filter_fields({})


def _backend() -> ScreenshotBackend:
    backend = ScreenshotBackend()
    backend.create_task(
        Task(
            task_id=1,
            title="Refine local-first sync confirmation",
            column_id=2,
            description="Explain exactly what Markdown saves locally and what Sync sends upstream.",
            due_date=date(2026, 8, 21),
            metadata={
                "key": "SCRUM-25",
                "issue_type": "Story",
                "assignee": "Alex Morgan",
                "reporter": "Platform team",
                "priority": "High",
                "labels": ["docs", "sync"],
                "components": ["Desktop", "Provider API"],
                "private_notes": "Verify conflict wording before provider Sync.",
            },
        )
    )
    backend.create_task(Task(task_id=2, title="Keep the Split layout visible", column_id=1))
    return backend


def _jira_backend() -> OfflineJiraBackend:
    backend = OfflineJiraBackend()
    backend.create_task(
        Task(
            task_id=25,
            title="Refine local-first sync confirmation",
            column_id=2,
            description="Explain exactly what stays in Markdown and what Sync sends to Jira.",
            due_date=date(2026, 8, 21),
            metadata={
                "key": "SCRUM-25",
                "jira_key": "SCRUM-25",
                "project": "SCRUM",
                "issue_type": "Story",
                "assignee": "Alex Morgan",
                "reporter": "Platform team",
                "priority": "High",
                "labels": ["docs", "sync"],
                "components": ["Desktop", "Provider API"],
                "private_notes": "Local review: verify conflict wording before provider Sync.",
                "url": "https://example.invalid/browse/SCRUM-25",
            },
        )
    )
    backend.create_task(Task(task_id=24, title="Keep the Split layout visible", column_id=1))
    return backend


def _open_editor(app: KanbanApp) -> WorkItemsView:
    """Return the mounted production Split editor or fail loudly."""

    view = app.query_one(WorkItemsView)
    if app.board_layout is not BoardLayout.SPLIT:
        raise RuntimeError(f"expected Split layout, found {app.board_layout!r}")
    if not view.editing:
        raise RuntimeError("inline editor is not active")
    if len(app.screen_stack) != 1:
        raise RuntimeError("inline editing unexpectedly pushed another screen")
    return view


def _geometry(app: KanbanApp, scroll: VerticalScroll, focus: Widget) -> dict[str, Any]:
    """Collect exact terminal-cell metrics for one verified capture."""

    view = app.query_one(WorkItemsView)
    left = app.query_one("#work-items-list-pane")
    divider = app.query_one("#work-item-resizer")
    right = app.query_one("#work-item-detail-pane")
    save = app.query_one("#work-item-edit-save", Button)
    cancel = app.query_one("#work-item-edit-cancel", Button)
    description = app.query_one("#work-item-edit-description", TextArea)
    private_notes = app.query_one("#work-item-edit-private-notes", TextArea)
    return {
        "terminal_cells": [app.screen.region.width, app.screen.region.height],
        "split_percent": view.list_percent,
        "rows_pane_cells": [left.region.width, left.region.height],
        "divider_cells": [divider.region.width, divider.region.height],
        "detail_pane_cells": [right.region.width, right.region.height],
        "scroll_viewport_cells": [scroll.content_region.width, scroll.content_region.height],
        "scroll_virtual_cells": [scroll.virtual_size.width, scroll.virtual_size.height],
        "scroll_offset_y": int(scroll.scroll_y),
        "scroll_max_y": scroll.max_scroll_y,
        "vertical_scrollbar_visible": scroll.show_vertical_scrollbar,
        "vertical_scrollbar_cells": scroll.scrollbar_size_vertical,
        "horizontal_scroll_max": scroll.max_scroll_x,
        "focused_id": focus.id,
        "focused_region": [focus.region.x, focus.region.y, focus.region.width, focus.region.height],
        "description_region": [
            description.region.x,
            description.region.y,
            description.region.width,
            description.region.height,
        ],
        "private_notes_region": [
            private_notes.region.x,
            private_notes.region.y,
            private_notes.region.width,
            private_notes.region.height,
        ],
        "save_region": [save.region.x, save.region.y, save.region.width, save.region.height],
        "cancel_region": [cancel.region.x, cancel.region.y, cancel.region.width, cancel.region.height],
        "screen_stack_depth": len(app.screen_stack),
        "inline_editor": view.editing,
    }


def _validate_geometry(
    app: KanbanApp,
    scroll: VerticalScroll,
    focus: Widget,
    *,
    require_scroll: bool,
) -> None:
    """Reject a visually misleading screenshot before writing the artifact."""

    _open_editor(app)
    divider = app.query_one("#work-item-resizer")
    right = app.query_one("#work-item-detail-pane")
    save = app.query_one("#work-item-edit-save", Button)
    cancel = app.query_one("#work-item-edit-cancel", Button)
    if divider.region.width != 1:
        raise RuntimeError(f"divider is {divider.region.width} cells, expected 1")
    if scroll.max_scroll_x != 0:
        raise RuntimeError(f"editor has horizontal overflow: {scroll.max_scroll_x}")
    description = app.query_one("#work-item-edit-description", TextArea)
    private_notes = app.query_one("#work-item-edit-private-notes", TextArea)
    if description.region.height and description.region.height < MIN_WRITING_HEIGHT:
        raise RuntimeError(
            f"Description is only {description.region.height} rows; expected at least {MIN_WRITING_HEIGHT}"
        )
    if private_notes.region.height and private_notes.region.height < MIN_WRITING_HEIGHT:
        raise RuntimeError(
            f"Private notes is only {private_notes.region.height} rows; expected at least {MIN_WRITING_HEIGHT}"
        )
    if save.region.right > right.content_region.right or cancel.region.right > right.content_region.right:
        raise RuntimeError("pinned editor action is clipped by the detail pane")
    if require_scroll:
        if scroll.max_scroll_y <= 0 or scroll.scroll_y <= 0:
            raise RuntimeError("short editor did not scroll vertically")
        if not scroll.show_vertical_scrollbar:
            raise RuntimeError(
                "short editor has no visible vertical scrollbar "
                f"(max_y={scroll.max_scroll_y}, size={scroll.scrollbar_size_vertical})"
            )
        if not focus.has_focus:
            raise RuntimeError("requested low provider field is not focused")
        viewport = scroll.content_region
        if focus.content_region.bottom <= viewport.y or focus.content_region.y >= viewport.bottom:
            raise RuntimeError("focused low provider field is outside the scroll viewport")
        if focus.content_region.height <= viewport.height and (
            focus.content_region.y < viewport.y or focus.content_region.bottom > viewport.bottom
        ):
            raise RuntimeError("focused low provider field is not fully visible")


def _save(app: KanbanApp, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    app.save_screenshot(str(path.resolve()))
    return path


def _popup_geometry(
    app: KanbanApp,
    screen: TaskDetailScreen,
    body: VerticalScroll,
    focus: TextArea,
) -> dict[str, Any]:
    """Collect popup geometry without relying on pixels or a mock image."""

    dialog = screen.query_one("#detail-dialog")
    description = screen.query_one("#detail-notes", TextArea)
    private_notes = screen.query_one("#detail-private-notes", TextArea)
    save = screen.query_one("#detail-primary", Button)
    close = screen.query_one("#detail-close", Button)
    return {
        "terminal_cells": [app.screen.region.width, app.screen.region.height],
        "dialog_cells": [dialog.region.x, dialog.region.y, dialog.region.width, dialog.region.height],
        "body_viewport_cells": [body.content_region.width, body.content_region.height],
        "body_virtual_cells": [body.virtual_size.width, body.virtual_size.height],
        "scroll_offset_y": int(body.scroll_y),
        "scroll_max_y": body.max_scroll_y,
        "vertical_scrollbar_visible": body.show_vertical_scrollbar,
        "vertical_scrollbar_cells": body.scrollbar_size_vertical,
        "horizontal_scroll_max": body.max_scroll_x,
        "description_region": [
            description.region.x,
            description.region.y,
            description.region.width,
            description.region.height,
        ],
        "private_notes_region": [
            private_notes.region.x,
            private_notes.region.y,
            private_notes.region.width,
            private_notes.region.height,
        ],
        "focused_id": focus.id,
        "focused_region": [focus.region.x, focus.region.y, focus.region.width, focus.region.height],
        "save_region": [save.region.x, save.region.y, save.region.width, save.region.height],
        "close_region": [close.region.x, close.region.y, close.region.width, close.region.height],
        "screen_stack_depth": len(app.screen_stack),
        "editing": screen.editing,
    }


def _validate_popup(
    app: KanbanApp,
    screen: TaskDetailScreen,
    body: VerticalScroll,
    focus: TextArea,
    *,
    require_scroll: bool,
) -> None:
    """Reject clipped popup captures or undersized writing areas."""

    dialog = screen.query_one("#detail-dialog")
    description = screen.query_one("#detail-notes", TextArea)
    private_notes = screen.query_one("#detail-private-notes", TextArea)
    save = screen.query_one("#detail-primary", Button)
    close = screen.query_one("#detail-close", Button)
    if not screen.editing:
        raise RuntimeError("detail popup is not editing")
    if description.region.height < MIN_WRITING_HEIGHT:
        raise RuntimeError(f"popup Description is only {description.region.height} rows")
    if private_notes.region.height < MIN_WRITING_HEIGHT:
        raise RuntimeError(f"popup Private notes is only {private_notes.region.height} rows")
    if body.max_scroll_x != 0:
        raise RuntimeError(f"popup has horizontal overflow: {body.max_scroll_x}")
    if body.scrollbar_size_vertical != 1:
        raise RuntimeError(f"popup scrollbar is {body.scrollbar_size_vertical} cells, expected 1")
    if save.region.bottom > dialog.content_region.bottom or close.region.bottom > dialog.content_region.bottom:
        raise RuntimeError("popup actions are clipped by the dialog")
    if save.region.bottom > app.screen.region.bottom or close.region.bottom > app.screen.region.bottom:
        raise RuntimeError("popup actions are clipped by the terminal")
    if save.region.y < body.region.bottom or close.region.y < body.region.bottom:
        raise RuntimeError("popup actions overlap the scrolling body")
    if require_scroll:
        if body.max_scroll_y <= 0 or body.scroll_y <= 0:
            raise RuntimeError("popup body did not scroll to the lower editor")
        if not body.show_vertical_scrollbar:
            raise RuntimeError("popup vertical scrollbar is not visible")
        if not focus.has_focus:
            raise RuntimeError("requested popup editor is not focused")
        viewport = body.content_region
        if focus.content_region.height <= viewport.height and (
            focus.content_region.y < viewport.y or focus.content_region.bottom > viewport.bottom
        ):
            raise RuntimeError("focused popup editor is not fully visible")


def _chrome() -> Path | None:
    """Return a deterministic local SVG renderer when installed."""

    discovered = shutil.which("chrome") or shutil.which("google-chrome")
    candidates = [
        Path(discovered) if discovered else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    return next((candidate for candidate in candidates if candidate and candidate.is_file()), None)


def _rasterise(svg_path: Path) -> Path | None:
    """Rasterize the genuine compositor SVG without changing its geometry."""

    png_path = svg_path.with_suffix(".png")
    png_path.unlink(missing_ok=True)
    try:
        import resvg_py
    except ImportError:
        pass
    else:
        png_path.write_bytes(resvg_py.svg_to_bytes(svg_path=str(svg_path)))
        return png_path

    try:
        import cairosvg
    except (ImportError, OSError):
        pass
    else:
        try:
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
        except OSError:
            pass
        else:
            return png_path

    renderer = _chrome()
    if renderer is None:
        return None
    view_box = svg_path.read_text(encoding="utf-8", errors="strict").split("viewBox=\"", 1)[1].split("\"", 1)[0]
    _x, _y, width, height = (round(float(value)) for value in view_box.split())
    with tempfile.TemporaryDirectory(prefix="pykantui-shot-") as profile:
        arguments = (
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            f"--user-data-dir={profile}",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            f"--screenshot={png_path.resolve()}",
            svg_path.resolve().as_uri(),
        )
        escaped = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        command = (
            f"$p=Start-Process -FilePath '{renderer}' -ArgumentList @({escaped}) "
            "-Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        if not result.returncode:
            for _ in range(100):
                if png_path.is_file():
                    break
                time.sleep(0.05)
    if result.returncode or not png_path.is_file():
        return None
    return png_path


async def _capture_editor(
    into: Path,
    *,
    size: tuple[int, int],
    name: str,
    tab_name: str,
    scroll_selector: str,
    focus_selector: str,
    prime_focus_selector: str | None = None,
    require_scroll: bool,
) -> tuple[Path, dict[str, Any]]:
    backend = _backend()
    with patch.object(backend, "available_task_fields", return_value=frozenset(WorkItemColumn)):
        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=size) as pilot:
            app.theme = "cyberpunk"
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            app.query_one(WorkItemTable).move_cursor(row=1)
            await pilot.pause()
            await app.query_one(WorkItemsView).start_inline_edit()
            await pilot.pause()
            app.query_one("#work-item-tabs", TabbedContent).active = f"work-item-{tab_name}-tab"
            await pilot.pause()

            scroll = app.query_one(scroll_selector, VerticalScroll)
            focus = app.query_one(focus_selector, Widget)
            if prime_focus_selector is not None:
                app.query_one(prime_focus_selector, Widget).focus()
                await pilot.pause()
            focus.focus()
            await pilot.pause()
            scroll.scroll_to_widget(focus, immediate=True, force=True)
            await pilot.pause()
            _validate_geometry(app, scroll, focus, require_scroll=require_scroll)
            metrics = _geometry(app, scroll, focus)
            svg = _save(app, into / f"{name}.svg")
    return svg, metrics


async def _capture_expanded_filters(into: Path) -> tuple[Path, dict[str, Any]]:
    """Capture Split beneath the real Jira filter shape at a short height."""

    with tempfile.TemporaryDirectory(prefix="pykantui-jira-filter-shot-"):
        app = KanbanApp(_jira_backend(), confirm_moves=False)
        async with app.run_test(size=FILTER_SIZE) as pilot:
            app.theme = "cyberpunk"
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.press("f2", "f2")
            await pilot.pause()
            app.query_one("#work-item-tabs", TabbedContent).active = "work-item-info-tab"
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            table = app.query_one("#work-items-table")
            pane = app.query_one("#work-item-detail-pane")
            filter_panel = app.query_one("#bar-panel", VerticalScroll)
            info = app.query_one("#work-item-info-read", VerticalScroll)
            private_notes = app.query_one("#work-item-private-notes", Widget)
            info.scroll_to_widget(private_notes, immediate=True, force=True)
            await pilot.pause()
            if view.region.height < 10 or table.region.height < 7 or pane.region.height < 10:
                raise RuntimeError(
                    "expanded Jira filters left an unusable Split workspace: "
                    f"view={view.region}, table={table.region}, pane={pane.region}"
                )
            if info.max_scroll_x != 0:
                raise RuntimeError(f"expanded-filter sidebar has horizontal overflow: {info.max_scroll_x}")
            if filter_panel.max_scroll_y <= 0 or not filter_panel.show_vertical_scrollbar:
                raise RuntimeError("expanded Jira filter panel did not expose vertical scrolling")
            if info.max_scroll_y <= 0 or not info.show_vertical_scrollbar:
                raise RuntimeError("expanded-filter sidebar did not expose vertical scrolling")
            metrics = {
                "terminal_cells": list(FILTER_SIZE),
                "workspace_cells": [view.region.width, view.region.height],
                "table_cells": [table.region.width, table.region.height],
                "detail_pane_cells": [pane.region.width, pane.region.height],
                "filter_viewport_cells": [
                    filter_panel.content_region.width,
                    filter_panel.content_region.height,
                ],
                "filter_virtual_cells": [
                    filter_panel.virtual_size.width,
                    filter_panel.virtual_size.height,
                ],
                "filter_scroll_max_y": filter_panel.max_scroll_y,
                "filter_scrollbar_visible": filter_panel.show_vertical_scrollbar,
                "filter_scrollbar_cells": filter_panel.scrollbar_size_vertical,
                "scroll_viewport_cells": [info.content_region.width, info.content_region.height],
                "scroll_virtual_cells": [info.virtual_size.width, info.virtual_size.height],
                "scroll_offset_y": int(info.scroll_y),
                "scroll_max_y": info.max_scroll_y,
                "vertical_scrollbar_visible": info.show_vertical_scrollbar,
                "vertical_scrollbar_cells": info.scrollbar_size_vertical,
                "horizontal_scroll_max": info.max_scroll_x,
            }
            svg = _save(app, into / f"{OUTPUT_NAMES['filters']}.svg")
    return svg, metrics


async def _capture_popup(
    into: Path,
    *,
    size: tuple[int, int],
    name: str,
) -> tuple[Path, dict[str, Any]]:
    """Capture the production edit popup scrolled to Private notes."""

    with tempfile.TemporaryDirectory(prefix="pykantui-jira-popup-shot-"):
        app = KanbanApp(_jira_backend(), confirm_moves=False)
        async with app.run_test(size=size) as pilot:
            app.theme = "cyberpunk"
            await pilot.pause()
            target = next(
                card
                for card in app.query(TaskCard).results()
                if card.task_.metadata.get("key") == "SCRUM-25"
            )
            target.focus()
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            if not isinstance(app.screen, TaskDetailScreen):
                raise RuntimeError(f"Edit did not open TaskDetailScreen: {type(app.screen).__name__}")
            screen = app.screen
            body = screen.query_one("#detail-body", VerticalScroll)
            private_notes = screen.query_one("#detail-private-notes", TextArea)
            private_notes.focus()
            await pilot.pause()
            body.scroll_to_widget(private_notes, immediate=True, force=True)
            await pilot.pause()
            _validate_popup(app, screen, body, private_notes, require_scroll=True)
            metrics = _popup_geometry(app, screen, body, private_notes)
            svg = _save(app, into / f"{name}.svg")
    return svg, metrics


def _validate_create_popup(
    app: KanbanApp,
    screen: TaskEditScreen,
    body: VerticalScroll,
    notes: TextArea,
    *,
    require_scroll: bool,
) -> None:
    """Reject undersized or clipped new-card captures."""

    dialog = screen.query_one("#edit-dialog")
    save = screen.query_one("#edit-save", Button)
    cancel = screen.query_one("#edit-cancel", Button)
    if notes.region.height < MIN_WRITING_HEIGHT:
        raise RuntimeError(f"new-card Notes is only {notes.region.height} rows")
    if body.max_scroll_x != 0:
        raise RuntimeError(f"new-card popup has horizontal overflow: {body.max_scroll_x}")
    if require_scroll and body.scrollbar_size_vertical != 1:
        raise RuntimeError(f"new-card scrollbar is {body.scrollbar_size_vertical} cells, expected 1")
    if save.region.bottom > dialog.content_region.bottom or cancel.region.bottom > dialog.content_region.bottom:
        raise RuntimeError("new-card actions are clipped by the dialog")
    if save.region.bottom > app.screen.region.bottom or cancel.region.bottom > app.screen.region.bottom:
        raise RuntimeError("new-card actions are clipped by the terminal")
    if save.region.y < body.region.bottom or cancel.region.y < body.region.bottom:
        raise RuntimeError("new-card actions overlap the scrolling body")
    if require_scroll:
        if body.max_scroll_y <= 0 or body.scroll_y <= 0:
            raise RuntimeError("new-card popup did not scroll to Notes")
        if not body.show_vertical_scrollbar:
            raise RuntimeError("new-card vertical scrollbar is not visible")
        if not notes.has_focus:
            raise RuntimeError("new-card Notes is not focused")
        viewport = body.content_region
        if notes.content_region.height <= viewport.height and (
            notes.content_region.y < viewport.y or notes.content_region.bottom > viewport.bottom
        ):
            raise RuntimeError("focused new-card Notes is not fully visible")


async def _capture_create_popup(
    into: Path,
    *,
    size: tuple[int, int],
    name: str,
    require_scroll: bool,
) -> tuple[Path, dict[str, Any]]:
    """Capture the production new-card form and its pinned actions."""

    with tempfile.TemporaryDirectory(prefix="pykantui-jira-create-shot-"):
        app = KanbanApp(_jira_backend(), confirm_moves=False)
        async with app.run_test(size=size) as pilot:
            app.theme = "cyberpunk"
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            if not isinstance(app.screen, TaskEditScreen):
                raise RuntimeError(f"New did not open TaskEditScreen: {type(app.screen).__name__}")
            screen = app.screen
            dialog = screen.query_one("#edit-dialog")
            body = screen.query_one("#edit-body", VerticalScroll)
            notes = screen.query_one("#edit-notes", TextArea)
            save = screen.query_one("#edit-save", Button)
            cancel = screen.query_one("#edit-cancel", Button)
            notes.focus()
            await pilot.pause()
            if require_scroll:
                body.scroll_to_widget(notes, immediate=True, force=True)
                await pilot.pause()
            _validate_create_popup(app, screen, body, notes, require_scroll=require_scroll)
            metrics = {
                "terminal_cells": [app.screen.region.width, app.screen.region.height],
                "dialog_cells": [dialog.region.x, dialog.region.y, dialog.region.width, dialog.region.height],
                "body_viewport_cells": [body.content_region.width, body.content_region.height],
                "body_virtual_cells": [body.virtual_size.width, body.virtual_size.height],
                "scroll_offset_y": int(body.scroll_y),
                "scroll_max_y": body.max_scroll_y,
                "vertical_scrollbar_visible": body.show_vertical_scrollbar,
                "vertical_scrollbar_cells": body.scrollbar_size_vertical,
                "horizontal_scroll_max": body.max_scroll_x,
                "notes_region": [notes.region.x, notes.region.y, notes.region.width, notes.region.height],
                "focused_id": notes.id,
                "focused_region": [notes.region.x, notes.region.y, notes.region.width, notes.region.height],
                "save_region": [save.region.x, save.region.y, save.region.width, save.region.height],
                "cancel_region": [cancel.region.x, cancel.region.y, cancel.region.width, cancel.region.height],
                "screen_stack_depth": len(app.screen_stack),
            }
            svg = _save(app, into / f"{name}.svg")
    return svg, metrics


async def _narrow_metrics() -> dict[str, Any]:
    backend = _backend()
    app = KanbanApp(backend, confirm_moves=False)
    async with app.run_test(size=NARROW_SIZE) as pilot:
        app.theme = "cyberpunk"
        await pilot.pause()
        app.set_board_layout(BoardLayout.SPLIT)
        await pilot.pause()
        app.query_one(WorkItemTable).move_cursor(row=1)
        await pilot.pause()
        await app.query_one(WorkItemsView).start_inline_edit()
        await pilot.pause()
        info = app.query_one("#work-item-info-edit", VerticalScroll)
        summary = app.query_one("#work-item-edit-summary", Widget)
        _validate_geometry(app, info, summary, require_scroll=False)
        return _geometry(app, info, summary)


async def render(into: Path) -> tuple[Path, ...]:
    """Write genuine Split/popup captures plus a geometry manifest."""

    normal_svg, normal = await _capture_editor(
        into,
        size=NORMAL_SIZE,
        name=OUTPUT_NAMES["normal"],
        tab_name="info",
        scroll_selector="#work-item-info-edit",
        focus_selector="#work-item-edit-summary",
        require_scroll=False,
    )
    short_info_svg, short_info = await _capture_editor(
        into,
        size=SHORT_SIZE,
        name=OUTPUT_NAMES["short_info"],
        tab_name="info",
        scroll_selector="#work-item-info-edit",
        focus_selector="#work-item-edit-private-notes",
        require_scroll=True,
    )
    short_details_svg, short_details = await _capture_editor(
        into,
        size=SHORT_SIZE,
        name=OUTPUT_NAMES["short_details"],
        tab_name="details",
        scroll_selector="#work-item-edit-scroll",
        focus_selector="#work-item-edit-components",
        prime_focus_selector="#work-item-edit-status",
        require_scroll=True,
    )
    filters_svg, filters = await _capture_expanded_filters(into)
    popup_normal_svg, popup_normal = await _capture_popup(
        into,
        size=POPUP_NORMAL_SIZE,
        name=OUTPUT_NAMES["popup_normal"],
    )
    popup_compact_svg, popup_compact = await _capture_popup(
        into,
        size=SHORT_SIZE,
        name=OUTPUT_NAMES["popup_compact"],
    )
    create_normal_svg, create_normal = await _capture_create_popup(
        into,
        size=POPUP_NORMAL_SIZE,
        name=OUTPUT_NAMES["create_normal"],
        require_scroll=False,
    )
    create_compact_svg, create_compact = await _capture_create_popup(
        into,
        size=SHORT_SIZE,
        name=OUTPUT_NAMES["create_compact"],
        require_scroll=True,
    )
    narrow = await _narrow_metrics()
    metrics_path = into / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "normal": normal,
                "short_info_scrolled": short_info,
                "short_details_scrolled": short_details,
                "expanded_jira_filters": filters,
                "popup_normal_scrolled": popup_normal,
                "popup_compact_scrolled": popup_compact,
                "create_normal": create_normal,
                "create_compact_scrolled": create_compact,
                "narrow_clamp": narrow,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    captures = (
        normal_svg,
        short_info_svg,
        short_details_svg,
        filters_svg,
        popup_normal_svg,
        popup_compact_svg,
        create_normal_svg,
        create_compact_svg,
    )
    generated: list[Path] = [*captures, metrics_path]
    for svg in captures:
        png = _rasterise(svg)
        if png is not None:
            generated.append(png)
    return tuple(generated)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--into",
        type=Path,
        default=Path("artifacts/split-sidebar-responsive"),
    )
    arguments = parser.parse_args()
    for path in asyncio.run(render(arguments.into)):
        print(path.resolve())


if __name__ == "__main__":
    main()
