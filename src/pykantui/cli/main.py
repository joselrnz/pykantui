"""Command line entry point.

Argument parsing and dispatch only. Each subcommand's work lives in
:mod:`pykantui.commands`, so adding one is a parser and a branch rather than
another few hundred lines here.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import cast

from pykantui import __version__
from pykantui.commands import columns as columns_command
from pykantui.commands import graph as graph_command
from pykantui.commands import init as init_command
from pykantui.commands import mcp as mcp_command
from pykantui.commands import new as new_command
from pykantui.commands import projects as projects_command
from pykantui.commands import sync as sync_command
from pykantui.commands import tasks as tasks_command
from pykantui.commands.launch import replace_with_workspace_board
from pykantui.config import BoardConfig, board_path, config_path, env, migrate_legacy_data
from pykantui.i18n import Locale, resolve_locale, using_locale
from pykantui.i18n import translate as _
from pykantui.models import Edges, MovementMode
from pykantui.models.task import Task
from pykantui.sync.base import Backend
from pykantui.sync.jsonstore import JsonBackend, demo_backend
from pykantui.workspace.status import SyncStatus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kbn", description=_("Terminal kanban board"))
    parser.add_argument("--version", action="version", version=f"pykantui {__version__}")
    parser.add_argument(
        "--locale",
        choices=[locale.value for locale in Locale],
        default=None,
        help=_("interface language; saved to config.json (default: auto)"),
    )
    parser.add_argument(
        "--movement",
        choices=[mode.value for mode in MovementMode],
        default=MovementMode.ADJACENT.value,
        help=_("adjacent commits H/L immediately; jump highlights a column and waits for enter"),
    )
    parser.add_argument(
        "--theme",
        default=None,
        help=_("theme name; saved to config.json (default: cyberpunk)"),
    )
    parser.add_argument(
        "--edges",
        choices=[edge.value for edge in Edges],
        default=None,
        help=_("corner style for cards, fields and dialogs; saved to config.json"),
    )
    parser.add_argument(
        "--no-confirm",
        dest="confirm",
        action="store_false",
        help=_("apply column moves without the confirmation dialog"),
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("demo", help=_("open a throwaway board with sample tasks"))

    board = sub.add_parser("board", help=_("open a local JSON board (the default)"))
    board.add_argument("--file", type=Path, default=None, help=_("path to the board file"))

    show = sub.add_parser("show", help=_("print the board as text, without starting the TUI"))
    show.add_argument("--file", type=Path, default=None)

    columns_command.add_parser(sub)
    tasks_command.add_parser(sub)
    init_command.add_parser(sub)
    sync_command.add_parser(sub)
    graph_command.add_parser(sub)
    new_command.add_parser(sub)
    projects_command.add_parser(sub)
    mcp_command.add_parser(sub)

    return parser


def _make_output_safe() -> None:
    """Stop a box-drawing character from killing the process on Windows.

    The default Windows console encoding is cp1252, which has no ``◌`` -- so
    printing a sync marker raised ``UnicodeEncodeError`` and took the whole
    command down *after* it had already written the files. A tool that does
    its work and then crashes while describing it is worse than one that
    fails outright.

    UTF-8 where the console supports it, and ``errors="replace"`` as the
    floor, so the worst case is a ``?`` instead of a traceback.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - a redirected pipe
            with contextlib.suppress(OSError, ValueError):
                reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _make_output_safe()
    migrate_legacy_data()

    # Before anything reads the environment. Providers take credentials from
    # variables like JIRA_TOKEN, and a gitignored .env beside the project is
    # the ordinary place to keep them. Real environment variables still win.
    env.load()

    arguments = list(sys.argv[1:] if argv is None else argv)
    locale = resolve_locale(_locale_argument(arguments), configured=_saved_locale())
    with using_locale(locale):
        return _run(arguments)


def _run(argv: list[str]) -> int:
    """Parse and dispatch while the selected locale context is active."""
    args = build_parser().parse_args(argv)
    _save_locale(args.locale)
    command = args.command or "board"

    if command == "init":
        _apply_global_appearance(theme=args.theme, edges=args.edges)
        return init_command.run(args)
    if command == "sync":
        return sync_command.run(args)
    if command == "graph":
        return graph_command.run(args)
    if command == "new":
        return new_command.run(args)
    if command == "projects":
        return projects_command.run(args)
    if command == "mcp":
        return mcp_command.run(args)
    if command == "columns":
        return columns_command.run(args)
    if command == "task":
        return tasks_command.run(args)
    if command == "show":
        return _show(args.file)
    try:
        backend = _backend_for(command, args)
    except (OSError, ValueError) as error:
        print(_("error: {error}").format(error=error), file=sys.stderr)
        return 2

    _apply_appearance(backend, theme=args.theme, edges=args.edges)

    # Import after locale resolution so Textual class-level descriptions use
    # the same language as widgets composed at runtime.
    from pykantui.tui.app import KanbanApp  # noqa: PLC0415

    selected_workspace = cast(
        Path | None,
        KanbanApp(
            backend=backend,
            movement_mode=MovementMode(args.movement),
            confirm_moves=args.confirm,
        ).run(),
    )
    if isinstance(selected_workspace, Path):
        replace_with_workspace_board(selected_workspace)
    return 0


def _locale_argument(argv: list[str]) -> str | None:
    """Read the global locale before constructing translated argparse help."""
    for index, argument in enumerate(argv):
        if argument.startswith("--locale="):
            return argument.partition("=")[2]
        if argument == "--locale" and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _saved_locale() -> Locale:
    """Read an existing preference without creating config for ``--help``."""
    target = config_path()
    if not target.exists():
        return Locale.AUTO
    try:
        return BoardConfig.load(target).locale
    except (OSError, ValueError):
        return Locale.AUTO


def _save_locale(raw: str | None) -> None:
    """Persist an explicit CLI choice in the user-level configuration."""
    if raw is None:
        return
    config = BoardConfig.load()
    config.locale = Locale(raw)
    config.save()


def _backend_for(command: str, args: argparse.Namespace) -> Backend:
    if command == "demo":
        return demo_backend()
    # Bare `kbn` reads the room, the way `git` does: inside a workspace it
    # opens that workspace's board, outside one it opens the local JSON board.
    # An explicit `kbn board` always means the JSON board, so there is still a
    # way to say it.
    # `--file` only exists when `board` was named explicitly. Bare `kbn` never
    # defines it, so reading `args.file` directly crashed the app's single most
    # common invocation with an AttributeError.
    wanted = getattr(args, "file", None)

    if command == "board" and wanted is None:
        workspace = _workspace_backend()
        if workspace is not None:
            return workspace

    return JsonBackend(path=wanted or board_path(), config=BoardConfig.load())


def _workspace_backend() -> Backend | None:
    """The board for the workspace we are standing in, if we are in one.

    Returns ``None`` rather than raising when there is no workspace -- that is
    the ordinary case, and it means "fall through to the local board".

    A broken workspace *is* reported, though. Silently opening an empty JSON
    board because ``project.json`` failed to parse would be a confusing way to
    find out something is wrong.
    """
    from pykantui.sync.provider import ProviderBackend  # noqa: PLC0415 - keeps the fast path light
    from pykantui.tracker.errors import ProviderError  # noqa: PLC0415
    from pykantui.workspace import layout  # noqa: PLC0415
    from pykantui.workspace.project import Project  # noqa: PLC0415

    root = layout.find_workspace()
    if root is None:
        return None

    try:
        project = Project.load(root)
        provider = project.open()
        return ProviderBackend(root, provider, project.remote(), column_style=project.column_style)
    except ProviderError as error:
        print(_("error: {error}").format(error=error), file=sys.stderr)
        print(_("(run `kbn sync` to fix, or `kbn board --file ...` for a local board)"), file=sys.stderr)
        raise SystemExit(2) from error


def _apply_appearance(backend: Backend, *, theme: str | None, edges: str | None) -> None:
    """Save the look-and-feel flags, so they are set once rather than every run."""
    config = backend.board_config()
    if config is None or not (theme or edges):
        return
    if theme:
        config.theme = theme
    if edges:
        config.edges = Edges(edges)
    config.save()


def _apply_global_appearance(*, theme: str | None, edges: str | None) -> None:
    """Persist appearance before standalone commands open their own apps.

    ``init`` launches the tracker and folder pickers before a backend exists,
    so waiting for :func:`_apply_appearance` silently discarded its global
    ``--theme`` and ``--edges`` options.
    """
    if not (theme or edges):
        return
    config = BoardConfig.load()
    if theme:
        config.theme = theme
    if edges:
        config.edges = Edges(edges)
    config.save()


#: Valid sync-state values, so an unrecognised one prints nothing instead of raising.
_SYNC_VALUES = {status.value for status in SyncStatus}


def _show(file: Path | None) -> int:
    """Render the board as plain text. Useful for scripts and for agents.

    Reads the room the same way bare ``kbn`` does: inside a workspace it prints
    that workspace's board, outside one it prints the local JSON board. It used
    to always print the JSON board, which meant standing in a workspace full of
    issues and being told the board was empty -- worst of all for the scripts
    and agents this exists for, which have no window to notice the difference.

    ``--file`` still forces the JSON board, so there is a way to ask for it.
    """
    backend: Backend | None = None
    if file is None:
        backend = _workspace_backend()
    if backend is None:
        backend = JsonBackend(path=file or board_path(), config=BoardConfig.load())

    tasks = backend.get_tasks()
    for column in backend.get_visible_columns():
        rows = sorted(
            (task for task in tasks if task.column_id == column.column_id),
            key=lambda task: task.position,
        )
        print(f"{column.name} ({len(rows)})")
        # Numbered by where they appear, not by `position`: a workspace board
        # carries no ordering of its own, so every card claimed to be first.
        for number, task in enumerate(rows, start=1):
            blocked = any(not t.finished for t in backend.get_tasks_by_ids(task.blocked_by))
            print(f"  {number}. {_one_line(task)}{' [blocked]' if blocked else ''}")
        print()
    return 0


def _one_line(task: Task) -> str:
    """One card on one line, without losing half of it.

    A workspace card's title is ``KEY\\nTitle`` so the TUI can set the key above
    the summary. Taking the first line -- which this did -- printed a column of
    bare issue keys and dropped every title.
    """
    title = " · ".join(part for part in task.title.splitlines() if part.strip())
    state = str(task.metadata.get("sync_status", "") or "")
    marker = SyncStatus(state).marker if state in _SYNC_VALUES else ""
    return f"{marker} {title}".strip()


if __name__ == "__main__":
    raise SystemExit(main())
