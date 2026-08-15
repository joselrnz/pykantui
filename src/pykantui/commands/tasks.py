"""``kbn task`` — put cards on the board without opening it.

Enough to seed a board, script it, or clean up afterwards. Editing a card is
still the TUI's job.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from pykantui.config import BoardConfig, ColumnConfig, board_path
from pykantui.i18n import translate as _
from pykantui.models import Task
from pykantui.sync.jsonstore import JsonBackend


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("add or remove cards")
    task = sub.add_parser("task", help=summary, description=summary)
    actions = task.add_subparsers(dest="action")

    add = actions.add_parser("add", help=_("add one card, or several numbered ones"))
    add.add_argument("title")
    add.add_argument("--column", default=None, help=_("column id, name or position (default: the first)"))
    add.add_argument("--count", type=int, default=1, help=_("add this many, numbered from 1"))
    add.add_argument("--description", default="", help=_("body text for the card"))
    add.add_argument("--due", default=None, metavar="YYYY-MM-DD", help=_("due date, or +N for N days from today"))
    add.add_argument(
        "--blocked-by",
        default="",
        metavar="IDS",
        help=_("comma-separated card ids that must finish first"),
    )
    add.add_argument("--file", type=Path, default=None)

    remove = actions.add_parser("rm", help=_("delete cards by id"))
    remove.add_argument("ids", nargs="+", type=int)
    remove.add_argument("--file", type=Path, default=None)

    clear = actions.add_parser("clear", help=_("delete every card in a column"))
    clear.add_argument("column")
    clear.add_argument("--yes", action="store_true", help=_("skip the confirmation"))
    clear.add_argument("--file", type=Path, default=None)


def run(args: argparse.Namespace) -> int:
    action = args.action
    if action is None:
        print("nothing to do; try: kbn task add --help", file=sys.stderr)
        return 1

    config = BoardConfig.load()
    backend = JsonBackend(path=args.file or board_path(), config=config)

    handlers = {"add": _add, "rm": _remove, "clear": _clear}
    return handlers[action](backend, config, args)


def _add(backend: JsonBackend, config: BoardConfig, args: argparse.Namespace) -> int:
    if args.count < 1:
        return _fail("--count has to be at least 1")

    column = _target_column(config, args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")

    try:
        due = _parse_due(args.due)
    except ValueError as error:
        return _fail(str(error))

    blocked_by = [int(part) for part in args.blocked_by.split(",") if part.strip()]
    unknown = [task_id for task_id in blocked_by if backend.get_task_by_id(task_id) is None]
    if unknown:
        return _fail(f"no card with id {', '.join(str(task_id) for task_id in unknown)}")

    # Zero-pad so 30 cards sort as 01..30 rather than 1, 10, 11, 2.
    width = len(str(args.count))
    created = 0
    for number in range(1, args.count + 1):
        title = args.title if args.count == 1 else f"{args.title} {number:0{width}d}"
        result = backend.create_task(
            Task(
                task_id=backend.next_task_id(),
                title=title,
                column_id=column.column_id,
                description=args.description,
                due_date=due,
                blocked_by=list(blocked_by),
            )
        )
        if not result.ok:
            return _fail(result.message)

        # Keep the other side of the dependency in step, so the blocking card
        # shows what it is holding up.
        for blocker_id in blocked_by:
            blocker = backend.get_task_by_id(blocker_id)
            if blocker is not None and result.task is not None and result.task.task_id not in blocker.blocking:
                blocker.blocking.append(result.task.task_id)
                backend.update_task(blocker)
        created += 1

    noun = "card" if created == 1 else "cards"
    print(f"added {created} {noun} to {column.name}")
    return 0


def _remove(backend: JsonBackend, config: BoardConfig, args: argparse.Namespace) -> int:
    del config
    missing = [task_id for task_id in args.ids if backend.get_task_by_id(task_id) is None]
    if missing:
        return _fail(f"no card with id {', '.join(str(task_id) for task_id in missing)}")

    for task_id in args.ids:
        backend.delete_task(task_id)
    noun = "card" if len(args.ids) == 1 else "cards"
    print(f"deleted {len(args.ids)} {noun}")
    return 0


def _clear(backend: JsonBackend, config: BoardConfig, args: argparse.Namespace) -> int:
    column = config.resolve(args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")

    doomed = [task for task in backend.get_tasks() if task.column_id == column.column_id]
    if not doomed:
        print(f"{column.name} is already empty")
        return 0
    if not args.yes:
        print(f"this deletes {len(doomed)} card(s) from {column.name}; re-run with --yes", file=sys.stderr)
        return 1

    for task in doomed:
        backend.delete_task(task.task_id)
    print(f"deleted {len(doomed)} card(s) from {column.name}")
    return 0


def _parse_due(raw: str | None) -> date | None:
    """Accept an absolute date or a relative ``+N`` / ``-N`` days."""
    if not raw:
        return None
    if raw[0] in "+-" and raw[1:].isdigit():
        return date.today() + timedelta(days=int(raw))
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is not a date; use YYYY-MM-DD or +N days") from None


def _target_column(config: BoardConfig, reference: str | None) -> ColumnConfig | None:
    if reference is not None:
        return config.resolve(reference)
    return next((column for column in config.ordered() if column.visible), None)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1
