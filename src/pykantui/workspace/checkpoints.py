"""Local Git recovery checkpoints around provider synchronization."""

from __future__ import annotations

from pathlib import Path

from pykantui import git
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import RemoteProject
from pykantui.workspace import layout
from pykantui.workspace.models import SyncReport
from pykantui.workspace.paths import ensure_workspace_path


def require_local_git_when_versioned(workspace: Path, commit: bool) -> None:
    git_marker = workspace / ".git"
    if not commit or not git_marker.exists():
        return
    ensure_workspace_path(workspace, git_marker)
    if not git.available():
        raise ProviderError(
            "Git is unavailable, so pykantui cannot create a local recovery version",
            hint="Install Git or rerun explicitly with local checkpointing disabled.",
        )
    if not git.ensure_runtime_ignored(workspace):
        raise ProviderError(
            "pykantui could not protect runtime lock files from local history",
            hint="Check the local repository permissions before syncing again.",
        )
    if not git.is_repo(workspace):
        raise ProviderError(
            "the workspace Git metadata is not a usable local repository",
            hint="Repair the local repository or explicitly disable local checkpointing.",
        )


def checkpoint_before_provider_write(
    workspace: Path,
    provider_name: str,
    project: RemoteProject,
    versioned: bool,
) -> None:
    paths = _checkpoint_paths(workspace, provider_name, project)
    try:
        dirty = versioned and git.is_dirty(workspace, paths=paths)
        committed = not dirty or git.commit(
            workspace,
            f"local({provider_name}/{project.slug()}): before sync",
            paths=paths,
        )
    except git.GitCommandError as error:
        raise ProviderError(
            "could not inspect local Git status before provider sync",
            hint="Fix the local Git error; no provider changes were sent.",
        ) from error
    if not committed:
        raise ProviderError(
            "could not create the local before-sync version",
            hint="Fix the local Git error before sending anything to the provider.",
        )


def checkpoint_after_sync(
    workspace: Path,
    provider_name: str,
    project: RemoteProject,
    report: SyncReport,
    versioned: bool,
) -> None:
    paths = _checkpoint_paths(workspace, provider_name, project)
    try:
        dirty = versioned and git.is_dirty(workspace, paths=paths)
        committed = not dirty or git.commit(
            workspace,
            f"sync({provider_name}/{project.slug()}): after sync · {report.summary()}",
            paths=paths,
        )
    except git.GitCommandError as error:
        raise ProviderError(
            "provider sync completed, but local Git status could not be inspected",
            hint="Your Markdown is intact. Repair Git and create a local commit before syncing again.",
        ) from error
    if not committed:
        raise ProviderError(
            "provider sync completed, but the local after-sync version failed",
            hint="Your Markdown is intact. Fix Git and create a local commit before syncing again.",
        )


def _checkpoint_paths(workspace: Path, provider_name: str, project: RemoteProject) -> tuple[Path, ...]:
    paths = [
        layout.meta_dir(workspace),
        layout.project_dir(workspace, provider_name, project),
    ]
    ignore = workspace / ".gitignore"
    if ignore.exists():
        paths.append(ignore)
    return tuple(paths)
