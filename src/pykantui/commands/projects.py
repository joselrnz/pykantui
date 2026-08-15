"""List and safely open workspaces registered under ``~/.pykantui``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from pykantui.commands.launch import replace_with_workspace_board
from pykantui.i18n import translate as _
from pykantui.pages import chooser
from pykantui.pages.chooser import Choice
from pykantui.tracker.errors import ProviderError
from pykantui.workspace.project import Project
from pykantui.workspace.registry import ProjectLink, ProjectRegistry, load_registry


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``kbn projects`` and its non-mutating ``open`` action."""

    summary = _("list or open registered project workspaces")
    parser = sub.add_parser("projects", help=summary, description=summary)
    actions = parser.add_subparsers(dest="projects_action")
    open_parser = actions.add_parser(
        "open",
        help=_("open a registered workspace"),
        description=_("open a registered workspace without syncing it"),
    )
    open_parser.add_argument(
        "query",
        nargs="?",
        help=_("exact provider/key, project id, name, or workspace path"),
    )


def run(args: argparse.Namespace) -> int:
    """List registered workspaces, or replace this process with one board."""

    try:
        registry = load_registry()
        validate_registry_workspaces(registry.projects)
        if getattr(args, "projects_action", None) != "open":
            return _list_projects(registry)
        selected = select_project(registry, getattr(args, "query", None))
        if selected is None:
            return 130
        workspace = validate_registered_workspace(selected)
        replace_with_workspace_board(workspace)
        return 0
    except ProviderError as error:
        print(_("error: {error}").format(error=error), file=sys.stderr)
        return 2


def select_project(registry: ProjectRegistry, query: str | None) -> ProjectLink | None:
    """Resolve an exact unique query, otherwise use the searchable chooser.

    No provider is constructed here. The registry is intentionally the only
    discovery source so opening an already-initialized board is instant and
    works offline.
    """

    projects = _ordered(registry.projects)
    if not projects:
        raise ProviderError(
            _("no registered project workspaces"),
            hint=_("Create one with `kbn init`."),
        )

    wanted = (query or "").strip()
    exact = [link for link in projects if wanted and _matches_exactly(link, wanted)]
    if len(exact) == 1:
        return exact[0]

    if wanted and not exact:
        candidates = [link for link in projects if _matches_partially(link, wanted)]
        if not candidates:
            candidates = projects
    else:
        candidates = exact or projects

    if not wanted and len(candidates) == 1:
        return candidates[0]

    if not chooser.can_run():
        if exact:
            raise ProviderError(
                _("{query!r} matches {count} registered workspaces").format(
                    query=wanted,
                    count=len(exact),
                ),
                hint=_("Pass an exact provider/key, project id, or workspace path."),
            )
        if wanted:
            raise ProviderError(
                _("no unique registered workspace matches {query!r}").format(query=wanted),
                hint=_(
                    "Run `kbn projects` and pass an exact provider/key, project id, or workspace path."
                ),
            )
        raise ProviderError(
            _("more than one project workspace is registered"),
            hint=_("Pass a provider/key, project id, name, or workspace path."),
        )

    picked = chooser.choose(
        project_choices(candidates),
        title=_("Which project workspace?"),
        filter_hint=_("type to filter by provider, project, or path"),
    )
    if picked is None:
        return None
    return next((link for link in candidates if link.workspace == picked), None)


def project_choices(projects: list[ProjectLink]) -> list[Choice]:
    """Render enough identity to distinguish duplicate provider names."""

    return [
        Choice(
            value=link.workspace,
            label=link.name or link.key or link.project_id,
            detail=f"{link.provider}/{link.key or link.project_id}",
            marker="●" if link.available else "○",
            tone="cyan" if link.available else "yellow",
            note=_("ready") if link.available else _("missing"),
            description=_("{workspace}\nid: {project_id}").format(
                workspace=link.workspace,
                project_id=link.project_id,
            ),
            keywords=(link.provider, link.project_id, link.key, link.name, link.workspace),
        )
        for link in _ordered(projects)
    ]


def validate_registered_workspace(link: ProjectLink) -> Path:
    """Return a canonical workspace only when its local metadata matches."""

    recorded = link.workspace_path.expanduser()
    if not recorded.is_absolute():
        raise ProviderError(
            _("registered workspace path is not absolute: {workspace}").format(workspace=recorded),
            hint=_("Repair projects.json with the workspace's canonical absolute path."),
        )
    if recorded.is_symlink():
        raise ProviderError(
            _("registered workspace is a symbolic link: {workspace}").format(workspace=recorded),
            hint=_("Run `kbn init` for the canonical destination instead."),
        )
    if not recorded.exists():
        raise ProviderError(
            _("registered workspace does not exist: {workspace}").format(workspace=recorded),
            hint=_("Move it back or initialize the new location with `kbn init`."),
        )
    if not recorded.is_dir():
        raise ProviderError(
            _("registered workspace is not a directory: {workspace}").format(workspace=recorded)
        )

    try:
        canonical = recorded.resolve(strict=True)
    except OSError as error:
        raise ProviderError(
            _("registered workspace cannot be resolved: {workspace}").format(workspace=recorded)
        ) from error

    try:
        project = Project.load(canonical)
    except ValidationError as error:
        raise ProviderError(
            _("registered workspace has invalid project metadata: {workspace}").format(
                workspace=canonical
            ),
            hint=_("Repair .pykantui/project.json or initialize the workspace again."),
        ) from error
    if (project.provider, project.project_id) != (link.provider, link.project_id):
        expected = f"{link.provider}/{link.project_id}"
        actual = f"{project.provider}/{project.project_id}"
        raise ProviderError(
            _(
                "registered workspace does not match its project metadata: "
                "expected {expected}, found {actual}"
            ).format(expected=expected, actual=actual),
            hint=_(
                "Do not open it through this registry entry; run `kbn init` for the intended project."
            ),
        )
    return canonical


def validate_registry_workspaces(projects: list[ProjectLink]) -> None:
    """Reject registry entries that violate one workspace/one project."""

    seen: dict[Path, ProjectLink] = {}
    for link in projects:
        canonical = link.workspace_path.expanduser().resolve(strict=False)
        previous = seen.get(canonical)
        if previous is not None:
            raise ProviderError(
                _("project registry contains the workspace more than once: {workspace}").format(
                    workspace=canonical
                ),
                hint=_("Keep exactly one project entry for each local workspace."),
            )
        seen[canonical] = link


def _list_projects(registry: ProjectRegistry) -> int:
    projects = _ordered(registry.projects)
    if not projects:
        print(_("No registered project workspaces. Run `kbn init` to create one."))
        return 0
    for link in projects:
        state = _("ready") if link.available else _("missing")
        print(f"{link.provider}/{link.key or link.project_id}  {link.name or '—'}")
        print(f"  {link.workspace}  [{state}]")
    return 0


def _ordered(projects: list[ProjectLink]) -> list[ProjectLink]:
    return sorted(
        projects,
        key=lambda link: (
            link.provider.casefold(),
            (link.key or link.project_id).casefold(),
            link.name.casefold(),
            link.workspace.casefold(),
        ),
    )


def _aliases(link: ProjectLink) -> tuple[str, ...]:
    identity = link.key or link.project_id
    return tuple(
        value
        for value in (
            link.workspace,
            str(link.workspace_path.expanduser().resolve(strict=False)),
            link.project_id,
            link.key,
            link.name,
            f"{link.provider}/{link.project_id}",
            f"{link.provider}/{identity}",
            f"{link.provider}/{link.name}" if link.name else "",
        )
        if value
    )


def _matches_exactly(link: ProjectLink, query: str) -> bool:
    wanted = query.casefold()
    return any(alias.casefold() == wanted for alias in _aliases(link))


def _matches_partially(link: ProjectLink, query: str) -> bool:
    wanted = query.casefold()
    return any(wanted in alias.casefold() for alias in _aliases(link))


__all__ = [
    "add_parser",
    "project_choices",
    "run",
    "select_project",
    "validate_registry_workspaces",
    "validate_registered_workspace",
]
