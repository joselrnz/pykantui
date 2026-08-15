"""Capture genuine Textual compositor evidence for local-first comments UI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, TabbedContent, TextArea

from pykantui.models import BoardLayout, MoveResult, Task
from pykantui.pages.detail import TaskDetailScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.models import CommentDraft, RemoteComment
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.comments import CommentsPane
from pykantui.tui.widgets.work_items import WorkItemsView

NORMAL_SIZE = (150, 40)
COMPACT_SIZE = (96, 18)
CommentItem = RemoteComment | CommentDraft


class CommentsBackend(JsonBackend):
    """Deterministic offline board exposing the neutral comment contract."""

    supports_sync = True

    def __init__(
        self,
        *,
        comments: dict[int, list[CommentItem]],
        add_comments: bool = True,
        label: str = "Jira",
    ) -> None:
        super().__init__()
        for task_id, title, column_id in (
            (1, "Refine local-first comment sync", 1),
            (2, "Keep provider writes explicit", 1),
            (3, "Review pending comment states", 2),
            (4, "Document comment cache behavior", 3),
            (5, "Verify short terminal layout", 4),
        ):
            self.create_task(Task(task_id=task_id, title=title, column_id=column_id))
        self.comments = comments
        self.add_comments = add_comments
        self.label = label

    def display_kind(self) -> str:
        return self.label

    def can_read_task_comments(self, task: Task) -> bool:
        del task
        return True

    def can_add_task_comment(self, task: Task) -> bool:
        del task
        return self.add_comments

    def get_task_comments(self, task: Task) -> tuple[CommentItem, ...]:
        return tuple(self.comments.get(task.task_id, ()))

    def save_comment_draft(self, task: Task, body: str) -> MoveResult:
        del task, body
        return MoveResult.failure("screenshot backend is read-only")

    def refresh_task_comments(self, task: Task) -> MoveResult:
        return MoveResult.success(task.model_copy(deep=True), "Comments refreshed")


def two_comments() -> dict[int, list[CommentItem]]:
    start = datetime(2026, 8, 13, 14, 15, tzinfo=UTC)
    return {
        1: [
            RemoteComment(
                comment_id="remote-1",
                issue_id="1",
                author="Ada Lovelace",
                created_at=start,
                body="First review note: keep private Markdown out of provider payloads.",
            ),
            RemoteComment(
                comment_id="remote-2",
                issue_id="1",
                author="Grace Hopper",
                created_at=start + timedelta(minutes=45),
                body="Approved. Sync can send this only after confirmation.",
            ),
        ]
    }


def _rasterise(svg_path: Path) -> Path | None:
    """Rasterize the exact compositor SVG when the local renderer is present."""
    try:
        import resvg_py
    except ImportError:
        pass
    else:
        png_path = svg_path.with_suffix(".png")
        png_path.write_bytes(resvg_py.svg_to_bytes(svg_path=str(svg_path)))
        return png_path

    discovered = shutil.which("chrome") or shutil.which("google-chrome")
    candidates = (
        Path(discovered) if discovered else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    )
    renderer = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
    if renderer is None:
        return None

    png_path = svg_path.with_suffix(".png")
    png_path.unlink(missing_ok=True)
    view_box = svg_path.read_text(encoding="utf-8").split('viewBox="', 1)[1].split('"', 1)[0]
    _x, _y, width, height = (round(float(value)) for value in view_box.split())
    with tempfile.TemporaryDirectory(prefix="pykantui-comments-shot-") as profile:
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
    return png_path if not result.returncode and png_path.is_file() else None


def _many_comments(count: int = 30) -> dict[int, list[CommentItem]]:
    start = datetime(2026, 8, 13, 9, 30, tzinfo=UTC)
    return {
        1: [
            RemoteComment(
                comment_id=f"comment-{number}",
                issue_id="1",
                author=f"Reviewer {number:02d}",
                created_at=start + timedelta(minutes=number * 7),
                body=f"Review note {number:02d}: keep this provider update explicit and local-first.",
            )
            for number in range(1, count + 1)
        ]
    }


async def _wait_for_entries(screen: Widget, selector: str, count: int) -> None:
    deadline = asyncio.get_running_loop().time() + 4
    while len(screen.query(selector)) != count:
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f"expected {count} comments at {selector}, got {len(screen.query(selector))}")
        await asyncio.sleep(0.02)


def _metrics(
    app: KanbanApp,
    pane: CommentsPane,
    thread: VerticalScroll,
    draft: TextArea,
    add: Button,
) -> dict[str, Any]:
    return {
        "terminal_cells": [app.size.width, app.size.height],
        "screen_stack_depth": len(app.screen_stack),
        "pane_region": [pane.region.x, pane.region.y, pane.region.width, pane.region.height],
        "thread_region": [thread.region.x, thread.region.y, thread.region.width, thread.region.height],
        "thread_virtual_cells": [thread.virtual_size.width, thread.virtual_size.height],
        "thread_scroll_y": int(thread.scroll_y),
        "thread_max_scroll_y": thread.max_scroll_y,
        "thread_max_scroll_x": thread.max_scroll_x,
        "vertical_scrollbar_cells": thread.styles.scrollbar_size_vertical,
        "vertical_scrollbar_visible": thread.show_vertical_scrollbar,
        "draft_region": [draft.region.x, draft.region.y, draft.region.width, draft.region.height],
        "add_region": [add.region.x, add.region.y, add.region.width, add.region.height],
        "comment_count": pane.comment_count,
    }


def _validate(pane: CommentsPane, thread: VerticalScroll, draft: TextArea, add: Button) -> None:
    if thread.max_scroll_x != 0:
        raise RuntimeError(f"comment thread has horizontal overflow: {thread.max_scroll_x}")
    if thread.styles.scrollbar_size_vertical != 1:
        raise RuntimeError("comment thread scrollbar is not one terminal cell")
    if draft.region.y < thread.region.bottom:
        raise RuntimeError("comment composer overlaps the thread")
    if add.region.y < draft.region.bottom:
        raise RuntimeError("Add locally overlaps the composer")
    if add.region.bottom > pane.content_region.bottom:
        raise RuntimeError("Add locally is clipped by the comments pane")


async def _capture_split(
    into: Path,
    *,
    name: str,
    size: tuple[int, int],
    backend: CommentsBackend,
    expected: int,
) -> tuple[Path, dict[str, Any]]:
    app = KanbanApp(backend, confirm_moves=False)
    async with app.run_test(size=size) as pilot:
        app.theme = "cyberpunk"
        await pilot.pause()
        app.set_board_layout(BoardLayout.SPLIT)
        await pilot.pause()
        view = app.query_one(WorkItemsView)
        view.action_focus_tab("comments")
        await _wait_for_entries(app.screen, ".provider-comment", expected)
        await pilot.pause()
        pane = view.query_one("#work-item-comments-pane", CommentsPane)
        thread = view.query_one("#work-item-comments-list", VerticalScroll)
        draft = view.query_one("#work-item-comment-draft", TextArea)
        add = view.query_one("#work-item-comment-add-local", Button)
        if not draft.disabled:
            draft.load_text("This draft stays local until Sync.")
            draft.focus()
        await pilot.pause()
        _validate(pane, thread, draft, add)
        svg = into / f"{name}.svg"
        app.save_screenshot(str(svg.resolve()))
        return svg, _metrics(app, pane, thread, draft, add)


async def _capture_popup(
    into: Path,
    *,
    name: str,
    size: tuple[int, int],
    layout: BoardLayout,
    backend: CommentsBackend,
    expected: int,
) -> tuple[Path, dict[str, Any]]:
    app = KanbanApp(backend, confirm_moves=False)
    async with app.run_test(size=size) as pilot:
        app.theme = "cyberpunk"
        await pilot.pause()
        app.set_board_layout(layout)
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        if not isinstance(app.screen, TaskDetailScreen):
            raise RuntimeError(f"expected TaskDetailScreen, got {type(app.screen).__name__}")
        await pilot.press("4")
        await _wait_for_entries(app.screen, ".provider-comment", expected)
        await pilot.pause()
        screen = app.screen
        if screen.query_one("#detail-tabs", TabbedContent).active != "detail-comments-tab":
            raise RuntimeError("popup Comments tab did not activate")
        pane = screen.query_one("#detail-comments-pane", CommentsPane)
        thread = screen.query_one("#detail-comments-list", VerticalScroll)
        draft = screen.query_one("#detail-comment-draft", TextArea)
        add = screen.query_one("#detail-comment-add-local", Button)
        close = screen.query_one("#detail-close", Button)
        if not draft.disabled:
            draft.load_text("Popup draft — still local until Sync.")
            draft.focus()
        await pilot.pause()
        _validate(pane, thread, draft, add)
        if add.region.bottom > close.region.y:
            raise RuntimeError("comment actions overlap the popup Edit/Close row")
        if close.region.bottom > screen.region.bottom:
            raise RuntimeError("popup Close action is clipped by the terminal")
        svg = into / f"{name}.svg"
        app.save_screenshot(str(svg.resolve()))
        values = _metrics(app, pane, thread, draft, add)
        values["close_region"] = [close.region.x, close.region.y, close.region.width, close.region.height]
        return svg, values


async def render(into: Path) -> tuple[Path, ...]:
    """Write SVGs, exact PNG rasterizations when available, and cell metrics."""
    into.mkdir(parents=True, exist_ok=True)
    captures = [
        await _capture_split(
            into,
            name="01-split-thread-150x40",
            size=NORMAL_SIZE,
            backend=CommentsBackend(comments=two_comments()),
            expected=2,
        ),
        await _capture_split(
            into,
            name="02-split-read-only-150x40",
            size=NORMAL_SIZE,
            backend=CommentsBackend(add_comments=False, label="Monday.com", comments=two_comments()),
            expected=2,
        ),
        await _capture_split(
            into,
            name="03-split-thread-96x18",
            size=COMPACT_SIZE,
            backend=CommentsBackend(comments=_many_comments()),
            expected=30,
        ),
        await _capture_popup(
            into,
            name="04-rows-popup-thread-150x40",
            size=NORMAL_SIZE,
            layout=BoardLayout.ROWS,
            backend=CommentsBackend(comments=two_comments()),
            expected=2,
        ),
        await _capture_popup(
            into,
            name="05-kanban-popup-thread-150x40",
            size=NORMAL_SIZE,
            layout=BoardLayout.KANBAN,
            backend=CommentsBackend(comments=two_comments()),
            expected=2,
        ),
        await _capture_popup(
            into,
            name="06-popup-thread-96x18",
            size=COMPACT_SIZE,
            layout=BoardLayout.KANBAN,
            backend=CommentsBackend(comments=_many_comments()),
            expected=30,
        ),
    ]
    metrics = {svg.stem: values for svg, values in captures}
    metrics_path = into / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return *(svg for svg, _values in captures), metrics_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, default=Path("artifacts/comments-ui"))
    arguments = parser.parse_args()
    generated = list(asyncio.run(render(arguments.into)))
    for svg in tuple(path for path in generated if path.suffix == ".svg"):
        if (png := _rasterise(svg)) is not None:
            generated.append(png)
    for path in generated:
        print(path.resolve())


if __name__ == "__main__":
    main()
