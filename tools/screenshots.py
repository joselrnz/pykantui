"""Render the README screenshots.

    python tools/screenshots.py            # every view, into assets/
    python tools/screenshots.py board card # just those two

Each view boots the real app against a throwaway in-memory board, drives it
with Textual's pilot, and writes an SVG. SVG rather than PNG because it is
text: it renders crisply at any size, GitHub displays it inline, and a diff
shows what actually changed instead of a wall of binary.

The board here is seeded to look like a real sprint rather than the three
cards the demo ships with — a screenshot of an empty board sells nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path

from textual.pilot import Pilot

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.models import BoardLayout, MenuLevel, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp

#: Wide enough for five columns and the three-across popup rows.
SIZE = (150, 42)

ASSETS = Path(__file__).resolve().parent.parent / "assets"

COLUMNS = ["To Do", "In Progress", "Needs Review", "Waiting", "Done"]

#: (id, column, title, assignee, priority, type, days until due)
CARDS: list[tuple[int, int, str, str, str, str, int | None]] = [
    (1, 1, "Set up payment logging", "Alex Morgan", "High", "Task", 5),
    (2, 1, "Rotate the Jira API token", "Alex Morgan", "Highest", "Task", 1),
    (3, 1, "Rate-limit the search endpoint", "Priya N", "Medium", "Story", 12),
    (4, 1, "Backfill the audit table", "Sam O", "Low", "Task", None),
    (5, 2, "Upgrade Postgres to 16", "Alex Morgan", "High", "Story", 3),
    (6, 2, "Fix timezone drift on due dates", "Priya N", "Medium", "Bug", -2),
    (7, 3, "Confirm the status names with QA", "Sam O", "Medium", "Task", 4),
    (8, 4, "Waiting on the vendor SSO answer", "Alex Morgan", "Low", "Task", None),
    (9, 5, "Ship 0.1.0", "Alex Morgan", "High", "Story", None),
    (10, 5, "Write the install docs", "Priya N", "Low", "Task", None),
]


def board_config() -> BoardConfig:
    """A config with no path, so nothing here can touch the real one."""
    return BoardConfig(
        columns=[ColumnConfig(column_id=i + 1, name=name, position=i) for i, name in enumerate(COLUMNS)],
        reset_column=1,
        start_column=2,
        finish_column=len(COLUMNS),
    )


class ScreenshotBackend(JsonBackend):
    """Provider-shaped, network-free backend used only by visual fixtures."""

    supports_sync = True

    def display_kind(self) -> str:
        return "Jira"


def seeded() -> JsonBackend:
    backend = ScreenshotBackend(config=board_config())
    for task_id, column, title, assignee, priority, kind, due in CARDS:
        backend.create_task(
            Task(
                task_id=task_id,
                title=title,
                column_id=column,
                description="Logged from the terminal, moved with one keystroke.",
                due_date=None if due is None else date.today() + timedelta(days=due),
                metadata={
                    "jira_key": f"SCRUM-{task_id}",
                    "assignee": assignee,
                    "priority": priority,
                    "issue_type": kind,
                    "reporter": "Alex Morgan",
                    "sprint": "SCRUM Sprint 3",
                    "labels": ["backend"] if kind != "Bug" else ["backend", "regression"],
                    "private_notes": "Local-only implementation notes.",
                    "sync_status": ("synced", "edited", "conflict", "new")[(task_id - 1) % 4],
                    "url": f"https://jira.example/browse/SCRUM-{task_id}",
                },
            )
        )
    return backend


async def settle(pilot: Pilot[None]) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


# ---- the views ---------------------------------------------------------


async def view_board(pilot: Pilot[None]) -> None:
    """The board as it opens."""


async def view_filters(pilot: Pilot[None]) -> None:
    """The top bar expanded to the full filter panel."""
    await pilot.press("f2")
    await pilot.press("f2")
    await settle(pilot)


async def view_card(pilot: Pilot[None]) -> None:
    """A card's details, read-only."""
    await pilot.press("v")
    await settle(pilot)


async def view_edit(pilot: Pilot[None]) -> None:
    """The same popup with the editable fields turned on."""
    await pilot.press("v")
    await settle(pilot)
    await pilot.press("e")
    await settle(pilot)


async def view_confirm(pilot: Pilot[None]) -> None:
    """The confirmation shown before a card changes column."""
    await pilot.press("L")
    await settle(pilot)


async def view_collapsed(pilot: Pilot[None]) -> None:
    """Two columns collapsed to strips."""
    await pilot.press("l", "l", "l", "z")
    await settle(pilot)
    await pilot.press("l", "z")
    await settle(pilot)


async def view_menu(pilot: Pilot[None]) -> None:
    """The column menu, opened from the keyboard."""
    await pilot.press("comma")
    await settle(pilot)


def _kanban(pilot: Pilot[None]) -> KanbanApp:
    """The pilot's app, narrowed from ``App[None]`` to the one we launched."""
    app = pilot.app
    assert isinstance(app, KanbanApp)
    return app


async def view_rows(pilot: Pilot[None]) -> None:
    """Dense JiraTUI-style work-item table with the sync gutter."""
    _kanban(pilot).set_board_layout(BoardLayout.ROWS)
    await settle(pilot)


async def view_split(pilot: Pilot[None]) -> None:
    """Work-item table and the provider-aware, cached detail tabs."""
    app = _kanban(pilot)
    app.set_board_layout(BoardLayout.SPLIT)
    app.menu_bar.level = MenuLevel.TOOLBAR
    await settle(pilot)


async def view_layout_menu(pilot: Pilot[None]) -> None:
    """The three selectable workspace layouts."""
    _kanban(pilot).menu_bar.level = MenuLevel.TOOLBAR
    await pilot.pause()
    await pilot.click("#bar-menu-view")
    # A popup screen intentionally remains open, so waiting for every app
    # worker here would wait for the screenshot subject to close itself.
    await pilot.pause()


async def view_palette(pilot: Pilot[None]) -> None:
    """The searchable global menu opened from its named header control."""
    await pilot.click("#app-header-menu")
    await pilot.pause()


VIEWS: dict[str, Callable[[Pilot[None]], Awaitable[None]]] = {
    "board": view_board,
    "filters": view_filters,
    "card": view_card,
    "edit": view_edit,
    "confirm": view_confirm,
    "collapsed": view_collapsed,
    "menu": view_menu,
    "rows": view_rows,
    "split": view_split,
    "layout-menu": view_layout_menu,
    "palette": view_palette,
}

#: The move confirmation is the one view that needs it turned on.
NEEDS_CONFIRM = {"confirm"}


async def render(name: str, theme: str, into: Path) -> Path:
    app = KanbanApp(backend=seeded(), confirm_moves=name in NEEDS_CONFIRM)
    config = app.backend.board_config()
    assert config is not None, "the JSON backend always carries a board config"
    config.theme = theme

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await VIEWS[name](pilot)
        target = into / f"{name}.svg"
        app.save_screenshot(str(target))

    return target


async def main(names: list[str], theme: str, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    for name in names:
        written = await render(name, theme, into)
        print(f"{name:10} -> {written.relative_to(written.parent.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("views", nargs="*", choices=[*VIEWS, []], help="views to render (default: all)")
    parser.add_argument("--theme", default="textual-dark", help="theme name (default: textual-dark)")
    parser.add_argument("--into", type=Path, default=ASSETS, help="output directory")
    args = parser.parse_args()

    asyncio.run(main(args.views or list(VIEWS), args.theme, args.into))
