"""``kbn new`` — draft a story as a markdown file. Nothing is sent.

A draft is an ordinary issue file with no key and a local id. It shows on the
board straight away as ``◌ not synced``, and stays entirely yours until a sync
is run and confirmed.

That separation is the whole point. Writing five stories should be five files
you can read, edit and delete, not five issues that appeared in a tracker
everyone else is looking at. Sending it to the provider is a second, deliberate step.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from pykantui.config.paths import write_text_atomic
from pykantui.i18n import translate as _
from pykantui.tracker import ProviderError
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import UnsupportedError
from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_TODO,
    IssueDraft,
    RemoteColumn,
    RemoteIssue,
    RemoteUser,
    slugify,
)
from pykantui.workspace import layout, markdown
from pykantui.workspace.cache import workspace_cache
from pykantui.workspace.paths import ensure_workspace_path
from pykantui.workspace.project import Project

#: Marks a file as never having reached a tracker. The sync looks for this
#: prefix to know a file is a create rather than an edit.
DRAFT_PREFIX = "draft-"


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("draft a story as markdown, without sending it")
    parser = sub.add_parser("new", help=summary, description=summary)
    parser.add_argument("title", nargs="*", help=_("the story title"))
    parser.add_argument("--path", type=Path, default=None, help=_("the workspace"))
    parser.add_argument("--column", default="", help=_("which column to draft it into"))
    parser.add_argument(
        "--type",
        dest="issue_type",
        default="",
        help=_("issue type; see --types for what this project accepts"),
    )
    parser.add_argument("--body", default="", help=_("the description"))
    parser.add_argument("--parent", default="", help=_("key of the epic it belongs to"))
    parser.add_argument("--label", action="append", default=[], help=_("repeatable"))
    parser.add_argument("--component", action="append", default=[], help=_("repeatable Jira component"))
    parser.add_argument("--priority", default="")
    parser.add_argument("--due", default="", help="YYYY-MM-DD")
    parser.add_argument("--columns", action="store_true", help=_("list the columns and exit"))
    parser.add_argument("--types", action="store_true", help=_("list the issue types and exit"))
    parser.add_argument("--components", action="store_true", help=_("list Jira components and exit"))
    parser.add_argument(
        "--unassigned",
        action="store_true",
        help=_("do not assign it to yourself (drafts are your work by default)"),
    )


def run(args: argparse.Namespace) -> int:
    try:
        workspace = _find(args.path)
        project = Project.load(workspace)

        with project.open() as provider:
            cache = workspace_cache(
                workspace,
                project.provider,
                project.remote(),
                credentials=provider.secrets,
            )
            provider.use_cache(cache)
            columns = provider.columns(project.project_id)
            if args.columns:
                return _list_columns(columns, provider.spec.label)
            if args.types:
                return _list_types(provider, project.project_id)
            if args.components:
                return _list_components(provider, project.project_id)
            if args.component and "components" not in provider.creatable_card_fields():
                raise UnsupportedError(f"{provider.spec.label} does not support card components")

            title = " ".join(args.title).strip()
            if not title:
                raise ProviderError("a story needs a title", hint='kbn new "Ship the thing"')

            column = _pick_column(columns, args.column)

            # Checked here rather than at push time: a typo'd type should fail
            # while you are still typing it, not once five files are written
            # and the sync is refusing them one by one.
            issue_type = provider.resolve_issue_type(project.project_id, args.issue_type)

            mine = _identity(provider) if not args.unassigned else None
            draft = IssueDraft(
                title=title,
                assignee_ids=(mine.account_id,) if mine and mine.account_id else (),
                assignee=mine.label() if mine else "",
                body=args.body,
                issue_type=issue_type.name if issue_type else "",
                column_id=column.column_id,
                column_name=column.name,
                priority=args.priority,
                labels=tuple(args.label),
                components=tuple(args.component),
                due_date=_parse_due(args.due),
                parent_key=args.parent,
            )
            path = write_draft(workspace, project, column, draft)
    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"drafted {path.relative_to(workspace)}")
    print()
    print("  nothing has been sent. next:")
    print("    kbn                 # see it on the board as ◌ not synced")
    print(f"    {_editor_hint(path)}")
    print("    kbn sync --dry-run  # what would be created")
    print("    kbn sync            # create it, after confirming")
    return 0


def write_draft(workspace: Path, project: Project, column: RemoteColumn, draft: IssueDraft) -> Path:
    """Write one draft file. Returns where it landed.

    The id is local and prefixed, never invented to look like a real one --
    a fake key would be indistinguishable from a synced issue the moment
    somebody read the file without the board next to it.
    """
    folder = ensure_workspace_path(
        workspace,
        layout.column_dir(workspace, project.provider, project.remote(), column, project.column_style),
    )
    folder.mkdir(parents=True, exist_ok=True)

    stem = f"{DRAFT_PREFIX}{draft.slug()}"
    path = ensure_workspace_path(workspace, folder / f"{stem}.md")
    suffix = 2
    while path.exists():
        path = ensure_workspace_path(workspace, folder / f"{stem}-{suffix}.md")
        suffix += 1

    issue = RemoteIssue(
        issue_id=path.stem,  # local, and visibly a draft
        key="",
        title=draft.title,
        column_id=column.column_id,
        status=column.name,
        assignee=draft.assignee,
        assignee_ids=draft.assignee_ids,
        body=draft.body,
        issue_type=draft.issue_type,
        priority=draft.priority,
        labels=draft.labels,
        due_date=draft.due_date,
        parent_key=draft.parent_key,
        created_at=datetime.now(),
    )
    write_text_atomic(
        ensure_workspace_path(workspace, path),
        markdown.render(
            issue,
            column_name=layout.column_folder(column, project.column_style),
            provider=project.provider,
        ),
    )
    return path


def is_draft(issue_id: str) -> bool:
    return issue_id.startswith(DRAFT_PREFIX)


# ---- helpers -------------------------------------------------------------


def _find(supplied: Path | None) -> Path:
    if supplied is not None:
        return supplied.expanduser().resolve()
    found = layout.find_workspace()
    if found is None:
        raise ProviderError(
            "not inside a pykantui workspace",
            hint="Run this from inside one, pass --path, or create one with: kbn init",
        )
    return found


def _identity(provider: Provider) -> RemoteUser | None:
    """Your id on this tracker, for assigning a draft to yourself.

    A story you sat down and wrote is your work, so it starts on your desk.
    This also closes a trap: a tracker sets only the *reporter* on create, so
    an unassigned new issue is somebody-nobody's the moment it exists and drops
    straight off an assigned-only board on the very next sync.

    Empty where the tracker cannot say who you are -- a Plane API key
    identifies a workspace, not a person -- in which case the issue is created
    unassigned rather than the draft failing.
    """
    try:
        return provider.verify()
    except (ProviderError, UnsupportedError):
        return None


def _list_columns(columns: list[RemoteColumn], label: str) -> int:
    print(f"{label} columns:")
    for column in columns:
        print(f"  {column.name:<18} {column.group}")
    return 0


def _list_types(provider: Provider, project_id: str) -> int:
    """What this project accepts, asked of the tracker rather than assumed.

    Sub-tasks are shown but flagged: they cannot be created without a parent,
    so offering one as a choice for a new story would be a dead end.
    """
    types = provider.issue_types(project_id)
    if not types:
        print(f"{provider.spec.label} does not expose issue types for this project.")
        print("  Draft without --type; the tracker decides.")
        return 0

    default = provider.default_issue_type(project_id)
    print(f"{provider.spec.label} issue types:")
    for item in types:
        marks = []
        if default is not None and item.name == default.name:
            marks.append("default")
        if item.subtask:
            marks.append("needs a parent")
        elif item.level > 0:
            marks.append("a container, not a story")
        suffix = f"  ({', '.join(marks)})" if marks else ""
        print(f"  {item.name:<18}{suffix}".rstrip())
    return 0


def _list_components(provider: Provider, project_id: str) -> int:
    """List project-scoped components from the provider's structural cache."""
    components = provider.components(project_id)
    if not components:
        print(f"{provider.spec.label} does not expose components for this project.")
        return 0
    print(f"{provider.spec.label} components:")
    for component in components:
        print(f"  {component.name:<24} {component.description}".rstrip())
    return 0


def _pick_column(columns: list[RemoteColumn], wanted: str) -> RemoteColumn:
    """Match a column by name, folder or group -- whichever the user typed.

    Defaults to the first column that means "not started". A draft belongs at
    the beginning of the board, and guessing the leftmost column is wrong on a
    board whose first lane is a backlog nobody works from.
    """
    if not columns:
        raise ProviderError("this project has no columns")

    if wanted:
        needle = wanted.strip().lower()
        for column in columns:
            if needle in (column.name.lower(), slugify(column.name), column.group):
                return column
        names = ", ".join(column.name for column in columns)
        raise ProviderError(f"no column matching {wanted!r}", hint=f"Columns: {names}")

    # The shared vocabulary, not any tracker's own words -- each provider maps
    # its real column names onto these, so this stays right for a board whose
    # first lane is called "Icebox". Constants rather than literals: a typo'd
    # group would silently never match, which is exactly what the validator on
    # RemoteColumn.group exists to prevent everywhere else.
    for group in (COLUMN_TODO, COLUMN_BACKLOG):
        found = next((column for column in columns if column.group == group), None)
        if found is not None:
            return found
    return columns[0]


def _parse_due(value: str) -> date | None:
    if not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise ProviderError(f"{value!r} is not a date", hint="Use YYYY-MM-DD.") from error


def _editor_hint(path: Path) -> str:
    return f'notepad "{path}"' if sys.platform == "win32" else f'$EDITOR "{path}"'
