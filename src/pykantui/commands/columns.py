"""``kbn columns`` — edit the board's shape from the command line.

Every command here loads the saved config, changes it, and writes it back, so
the result survives the process. Nothing assumes a particular number of columns.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pykantui.config import BoardConfig, ColumnConfig, board_path, config_path, default_config
from pykantui.i18n import translate as _
from pykantui.models import ColumnRole
from pykantui.sync.jsonstore import JsonBackend

#: What ``kbn columns role`` accepts, straight off the enum, so a new role can
#: never be added without the command line learning about it.
ROLES = tuple(role.value for role in ColumnRole)


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("add, rename, reorder or delete board columns")
    columns = sub.add_parser("columns", help=summary, description=summary)
    actions = columns.add_subparsers(dest="action")

    actions.add_parser("list", help=_("show the current columns (the default)"))

    add = actions.add_parser("add", help=_("add a column"))
    add.add_argument("name")
    add.add_argument("--after", default=None, help=_("column id, name or position to insert after"))
    add.add_argument("--statuses", default="", help=_("comma-separated provider statuses for this column"))
    add.add_argument("--hidden", action="store_true", help=_("create it hidden"))

    rename = actions.add_parser("rename", help=_("rename a column"))
    rename.add_argument("column")
    rename.add_argument("name")

    remove = actions.add_parser("remove", help=_("delete a column and rehome its cards"))
    remove.add_argument("column")
    remove.add_argument("--move-to", default=None, help=_("where its cards go (default: the first column)"))

    move = actions.add_parser("move", help=_("change a column's position"))
    move.add_argument("column")
    move.add_argument("position", type=int, help=_("1-based position"))

    role = actions.add_parser("role", help=_("set which column means reset, start or finish"))
    role.add_argument("role", choices=[*ROLES, "none"])
    role.add_argument("column", nargs="?", default=None)

    statuses = actions.add_parser("statuses", help=_("set the provider statuses that land in a column"))
    statuses.add_argument("column")
    statuses.add_argument("statuses", help=_("comma-separated; empty string clears them"))

    visible = actions.add_parser("show", help=_("make a hidden column visible"))
    visible.add_argument("column")
    hidden = actions.add_parser("hide", help=_("hide a column without deleting it"))
    hidden.add_argument("column")

    count = actions.add_parser(
        "count",
        help=_("set the number of visible columns, adding or removing at the end"),
    )
    count.add_argument("count", type=int)

    reset = actions.add_parser("reset", help=_("restore the default shape"))
    reset.add_argument("--yes", action="store_true", help=_("skip the confirmation"))


def run(args: argparse.Namespace) -> int:
    action = args.action or "list"
    config = BoardConfig.load()

    handlers = {
        "list": _list,
        "add": _add,
        "rename": _rename,
        "remove": _remove,
        "move": _move,
        "role": _role,
        "statuses": _statuses,
        "show": _show,
        "hide": _hide,
        "count": _count,
        "reset": _reset,
    }
    return handlers[action](config, args)


# ---- commands -----------------------------------------------------------


def _list(config: BoardConfig, args: argparse.Namespace) -> int:
    del args
    order = config.ordered()
    if not order:
        print("no columns configured; run: kbn columns reset")
        return 0

    width = max(len(column.name) for column in order)
    for position, column in enumerate(order, start=1):
        role = config.role_of(column.column_id)
        flags = [flag for flag in (role, None if column.visible else "hidden") if flag]
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        statuses = f"  {', '.join(column.jira_statuses)}" if column.jira_statuses else ""
        print(f"{position:>2}. #{column.column_id:<3} {column.name:<{width}}{suffix}{statuses}".rstrip())
    return 0


def _add(config: BoardConfig, args: argparse.Namespace) -> int:
    if config.find_by_name(args.name) is not None:
        return _fail(f"a column called {args.name!r} already exists")

    after = None
    if args.after is not None:
        after = config.resolve(args.after)
        if after is None:
            return _fail(f"no column matching {args.after!r}")

    column = config.add(
        args.name,
        after=after,
        statuses=_split(args.statuses),
        visible=not args.hidden,
    )
    config.save()
    print(f"added #{column.column_id} {column.name} at position {column.position + 1}")
    return _list(config, args)


def _rename(config: BoardConfig, args: argparse.Namespace) -> int:
    column = config.resolve(args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")
    was, column.name = column.name, args.name
    config.save()
    print(f"renamed {was} to {column.name}")
    return 0


def _remove(config: BoardConfig, args: argparse.Namespace) -> int:
    column = config.resolve(args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")
    if len(config.columns) == 1:
        return _fail("a board needs at least one column")
    if column.visible and sum(1 for other in config.columns if other.visible) == 1:
        return _fail("at least one column has to stay visible")

    destination = config.resolve(args.move_to) if args.move_to else None
    if args.move_to and destination is None:
        return _fail(f"no column matching {args.move_to!r}")
    if destination is None:
        destination = next(other for other in config.ordered() if other.column_id != column.column_id)

    moved = _rehome_tasks(column.column_id, destination.column_id)
    role = config.role_of(column.column_id)
    config.remove(column)
    config.save()

    print(f"removed #{column.column_id} {column.name}")
    if moved:
        print(f"moved {moved} card(s) to {destination.name}")
    if role:
        print(f"note: it was the {role} column; set a new one with: kbn columns role {role} <column>")
    return 0


def _move(config: BoardConfig, args: argparse.Namespace) -> int:
    column = config.resolve(args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")
    config.move(column, args.position)
    config.save()
    return _list(config, args)


def _role(config: BoardConfig, args: argparse.Namespace) -> int:
    if args.role == "none":
        return _fail("say which role to clear, e.g.: kbn columns role finish")

    if args.column is None:
        config.set_role(args.role, None)
        config.save()
        print(f"cleared the {args.role} column")
        return 0

    column = config.resolve(args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")
    config.set_role(args.role, column.column_id)
    config.save()
    print(f"{args.role} column is now {column.name}")
    return 0


def _statuses(config: BoardConfig, args: argparse.Namespace) -> int:
    column = config.resolve(args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")

    wanted = _split(args.statuses)
    clash = _first_clash(config, column, wanted)
    if clash is not None:
        status, owner = clash
        return _fail(f"{status!r} is already mapped to {owner.name}; remove it there first")

    column.jira_statuses = wanted
    config.save()
    print(f"{column.name}: {', '.join(wanted) if wanted else '(no statuses)'}")
    return 0


def _show(config: BoardConfig, args: argparse.Namespace) -> int:
    return _set_visible(config, args, True)


def _hide(config: BoardConfig, args: argparse.Namespace) -> int:
    return _set_visible(config, args, False)


def _set_visible(config: BoardConfig, args: argparse.Namespace, visible: bool) -> int:
    column = config.resolve(args.column)
    if column is None:
        return _fail(f"no column matching {args.column!r}")
    if not visible and sum(1 for other in config.columns if other.visible) == 1:
        return _fail("at least one column has to stay visible")
    column.visible = visible
    config.save()
    print(f"{column.name} is now {'visible' if visible else 'hidden'}")
    return 0


def _count(config: BoardConfig, args: argparse.Namespace) -> int:
    """Grow or shrink the board to ``count`` visible columns."""
    if args.count < 1:
        return _fail("a board needs at least one column")

    visible = [column for column in config.ordered() if column.visible]
    while len(visible) < args.count:
        # Insert after the last visible column rather than at the very end, so
        # new columns do not land behind hidden ones like Archive.
        added = config.add(f"Column {len(visible) + 1}", after=visible[-1] if visible else None)
        visible.append(added)

    removed = 0
    while len(visible) > args.count:
        column = visible.pop()
        _rehome_tasks(column.column_id, visible[-1].column_id)
        config.remove(column)
        removed += 1

    config.save()
    if removed:
        print(f"removed {removed} column(s); their cards moved left")
    return _list(config, args)


def _reset(config: BoardConfig, args: argparse.Namespace) -> int:
    del config
    if not args.yes:
        print("this replaces your columns with the defaults; re-run with --yes", file=sys.stderr)
        return 1
    fresh = default_config()
    fresh.save(config_path())
    print("restored the default columns")
    return _list(fresh, args)


# ---- helpers ------------------------------------------------------------


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _first_clash(config: BoardConfig, column: ColumnConfig, statuses: list[str]) -> tuple[str, ColumnConfig] | None:
    """A status may only map to one column, or the board is ambiguous."""
    for status in statuses:
        for other in config.columns:
            if other.column_id == column.column_id:
                continue
            if any(existing.casefold() == status.casefold() for existing in other.jira_statuses):
                return status, other
    return None


def _rehome_tasks(from_column: int, to_column: int, path: Path | None = None) -> int:
    """Move any local cards out of a column that is about to disappear."""
    target = path or board_path()
    if not target.exists():
        return 0

    backend = JsonBackend(path=target)
    stranded = [task for task in backend.get_tasks() if task.column_id == from_column]
    for task in stranded:
        task.column_id = to_column
        backend.update_task(task)
    return len(stranded)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1
