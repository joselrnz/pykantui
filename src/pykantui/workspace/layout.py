"""Where everything sits on disk.

One shape for every tracker::

    <workspace>/<provider>/projects/<owner?>/<project>/<column>/<key>.md

The uniformity is deliberate. Bespoke layouts would mean separate exporters,
file-tree widgets and interactions to learn; everything that genuinely varies
between trackers is already declared in the provider's spec and reaches this
module as data -- what fills the ``<project>`` slot, what the columns are
called, what a key looks like.

**The directory is the truth about which column a card is in.** Frontmatter
carries a ``column:`` line too, but it is written *from* the path, never read
back as authority. Dragging a file from ``to-do/`` to ``done/`` in any file
manager is the obvious gesture for moving a card, and it has to work.

**Column directories are lowercase and dashed** -- ``in-progress/``, not
``In Progress/``. See :class:`ColumnStyle`; a space in a path fails quietly
rather than loudly, which is the worst way for something to fail.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from urllib.parse import quote

from pykantui.core.naming import safe_name
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import RemoteColumn, RemoteIssue, RemoteProject, slugify

#: Everything pykantui owns inside a workspace. Kept in one dotted directory so
#: the rest of the tree is nothing but the markdown a human came for.
META_DIR = ".pykantui"

PROJECT_FILE = "project.json"
STATE_FILE = "state.json"
PENDING_CREATES_FILE = "pending-creates.json"
PENDING_COMMENTS_FILE = "pending-comments.json"
TRASH_DIR = "trash"
PROJECTS_DIR = "projects"

#: Legacy cache location retained in ``.gitignore`` so workspaces made by an
#: older pykantui release never accidentally commit response data. New cache
#: entries live under ``~/.pykantui/cache``.
CACHE_DIR = "cache"

#: Written at the root of each project directory, regenerated on every sync.
BOARD_FILE = "PROJECT.md"


def meta_dir(workspace: Path) -> Path:
    return workspace / META_DIR


def project_file(workspace: Path) -> Path:
    return meta_dir(workspace) / PROJECT_FILE


def state_file(workspace: Path) -> Path:
    return meta_dir(workspace) / STATE_FILE


def pending_creates_file(workspace: Path) -> Path:
    """Journal of create requests that may have succeeded remotely."""

    return meta_dir(workspace) / PENDING_CREATES_FILE


def pending_comments_file(workspace: Path) -> Path:
    """Journal of comment requests that may have succeeded remotely."""

    return meta_dir(workspace) / PENDING_COMMENTS_FILE


def trash_dir(workspace: Path) -> Path:
    """Recoverable local files removed before they ever reached a provider."""
    return meta_dir(workspace) / TRASH_DIR


def cache_dir(workspace: Path) -> Path:
    """Former workspace cache path, retained for compatibility and cleanup."""

    return meta_dir(workspace) / CACHE_DIR


def find_workspace(start: Path | None = None) -> Path | None:
    """The workspace containing ``start``, found by walking up.

    The way git finds ``.git``, and for the same reason: you should be able to
    run ``kbn`` from anywhere inside a project rather than only at its root.
    Returns ``None`` outside a workspace, which the caller reads as "fall back
    to the global board".
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / META_DIR / PROJECT_FILE).is_file():
            return candidate
    return None


def project_dir(workspace: Path, provider: str, project: RemoteProject) -> Path:
    """The directory holding one project's columns.

    ``path_parts`` is what lets GitHub nest by owner while everyone
    else stays a single segment -- see
    :meth:`~pykantui.tracker.models.RemoteProject.path_parts`.
    """
    return workspace.joinpath(safe_name(provider), PROJECTS_DIR, *project.path_parts())


class ColumnStyle(StrEnum):
    """How a column's name becomes a directory name.

    Real trackers name columns "In Progress", and a space in a path is a real
    cost: it needs quoting in every shell command, it has to be percent-encoded
    in every markdown link, and it is the classic source of a script that works
    until someone renames a column.

    Both are supported, and the default is the safe one:

    ``SLUG`` (default)
        ``in-progress/``. Lowercase, dashes for spaces. Types without quoting,
        survives every shell and script, needs no encoding in a markdown link,
        and behaves identically on a case-insensitive filesystem and a
        case-sensitive one.
    ``NAME``
        ``In Progress/``. The folder matches the board exactly, at the cost of
        quoting every path and percent-encoding every link.

    Slug is the default because the failure modes of a space are quiet ones: a
    dead markdown link renders as plain text rather than erroring, and a script
    that forgot to quote a path fails only once someone adds a column with a
    space in it. Matching the board exactly is worth something, but not that.
    """

    NAME = "name"
    SLUG = "slug"


#: Lowercase and dashed. See :class:`ColumnStyle` for why this is the default.
DEFAULT_COLUMN_STYLE = ColumnStyle.SLUG


def column_folder(column: RemoteColumn, style: ColumnStyle = DEFAULT_COLUMN_STYLE) -> str:
    """The directory name for one column, under the chosen style.

    Falls back to :func:`~pykantui.tracker.models.safe_name` when slugifying
    leaves nothing -- a column named only with symbols or non-ASCII would
    otherwise produce an empty path segment, which is not a directory at all.
    """
    raw = column.name or column.column_id
    if style is ColumnStyle.SLUG:
        return slugify(raw, limit=60) or safe_name(raw)
    return safe_name(raw)


def column_dir(
    workspace: Path,
    provider: str,
    project: RemoteProject,
    column: RemoteColumn,
    style: ColumnStyle = DEFAULT_COLUMN_STYLE,
) -> Path:
    """The directory for one column."""
    return project_dir(workspace, provider, project) / column_folder(column, style)


def folder_index(columns: list[RemoteColumn], style: ColumnStyle = DEFAULT_COLUMN_STYLE) -> dict[str, RemoteColumn]:
    """Map directory name back to column, for reading a moved file.

    Built with the same function that wrote the directories, so the two cannot
    disagree -- looking a folder up by ``column.name`` would silently stop
    matching the moment the style is set to ``SLUG``.
    """
    index: dict[str, RemoteColumn] = {}
    canonical: dict[str, tuple[str, RemoteColumn]] = {}
    for column in columns:
        folder = column_folder(column, style)
        collision_key = folder.casefold()
        previous = canonical.get(collision_key)
        if previous is not None and previous[1].column_id != column.column_id:
            previous_folder, previous_column = previous
            raise ProviderError(
                f"columns {previous_column.name!r} ({previous_column.column_id}) and "
                f"{column.name!r} ({column.column_id}) both map to local folder {previous_folder!r}",
                hint="Rename one provider column before syncing this project.",
            )
        canonical[collision_key] = folder, column
        index[folder] = column
    return index


def issue_path(
    workspace: Path,
    provider: str,
    project: RemoteProject,
    column: RemoteColumn,
    issue: RemoteIssue,
    style: ColumnStyle = DEFAULT_COLUMN_STYLE,
) -> Path:
    return column_dir(workspace, provider, project, column, style) / issue.filename()


def column_name_of(path: Path, workspace: Path, provider: str, project: RemoteProject) -> str:
    """Which column a file currently sits in, read from its path.

    This is how a card moves: the file is dragged to another folder and the
    next sync sees it. Returns ``""`` when the file is not where a card should
    be, so a stray markdown file somewhere in the tree is ignored rather than
    being pushed to the tracker as a mysterious status change.
    """
    root = project_dir(workspace, provider, project)
    try:
        relative = path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return ""
    # Exactly <column>/<file>. Anything deeper or shallower is not a card.
    return relative.parts[0] if len(relative.parts) == 2 else ""


def link_to(*parts: str) -> str:
    """A markdown link destination that survives a space.

    ``[JPT-4](In Progress/JPT-4.md)`` is not a link: CommonMark ends the
    destination at the first space, so it renders as literal text and every
    reference into a spaced column is dead. Percent-encoding is the fix that
    works in GitHub, in editors, and in a ``file://`` preview alike -- angle
    brackets are also valid CommonMark but far less widely supported.
    """
    return quote("/".join(parts))


def iter_issue_files(workspace: Path, provider: str, project: RemoteProject) -> list[Path]:
    """Every markdown file that is in a column directory of this project.

    Sorted, so a sync's output and its commits are deterministic rather than
    depending on the order the filesystem happens to hand things back.
    """
    root = project_dir(workspace, provider, project)
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    found: list[Path] = []
    for column in sorted(root.iterdir()):
        # Provider-controlled names must never turn a symlink into an input
        # channel for files outside this workspace.
        if column.is_symlink() or not column.is_dir():
            continue
        for path in sorted(column.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                path.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                continue
            found.append(path)
    return found
