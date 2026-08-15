"""Local repository operations for workspace versioning.

Git history is an on-device recovery mechanism around provider sync. These
operations intentionally provide no path to a remote repository.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from pykantui.config.paths import write_text_atomic

from .runner import GitCommandError, run_git

GitStatus = tuple[str, str]
_RUNTIME_PATHS = (".pykantui/sync.lock", ".pykantui/sync.lock.owner.json")


def is_repo(path: Path) -> bool:
    """Return whether ``path`` is a repository root, not merely inside one."""
    try:
        result = run_git(path, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    try:
        return Path(result.stdout.strip()).resolve() == path.resolve()
    except OSError:
        return False


def init(path: Path) -> bool:
    """Create a local repository at ``path`` when one does not exist."""
    if is_repo(path):
        return ensure_runtime_ignored(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        return run_git(path, "init", "-b", "main").returncode == 0 and ensure_runtime_ignored(path)
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_runtime_ignored(repository: Path) -> bool:
    """Exclude synchronization locks using repository-local Git metadata.

    ``.git/info/exclude`` is never committed or shared. It protects both new
    workspaces and older workspaces whose tracked ``.gitignore`` predates
    process-owner metadata.
    """
    try:
        result = run_git(repository, "rev-parse", "--git-path", "info/exclude")
        if result.returncode != 0 or not result.stdout.strip():
            return False
        exclude = Path(result.stdout.strip())
        if not exclude.is_absolute():
            exclude = repository / exclude
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        patterns = (".pykantui/*.lock", ".pykantui/*.lock.owner.json")
        missing = [pattern for pattern in patterns if pattern not in existing]
        if missing:
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            write_text_atomic(exclude, existing + prefix + "\n".join(missing) + "\n")
        # Repair workspaces made by versions that accidentally staged owner
        # metadata. Ignore rules do not affect an already tracked path.
        return (
            run_git(
                repository,
                "rm",
                "--cached",
                "--ignore-unmatch",
                "--",
                *_RUNTIME_PATHS,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _literal_pathspecs(repository: Path, paths: Iterable[str | Path]) -> tuple[str, ...]:
    """Return contained, literal Git pathspecs for application-owned paths."""
    root = repository.resolve()
    pathspecs: list[str] = []
    for supplied in paths:
        candidate = Path(supplied)
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(f"Git path is outside the workspace: {supplied}") from error
        if not relative.parts:
            raise ValueError("refusing to treat the whole workspace as an owned path")
        pathspecs.append(f":(literal){relative.as_posix()}")
    return tuple(pathspecs)


def status(repository: Path, *, paths: Iterable[str | Path] | None = None) -> list[GitStatus]:
    """Return ``(code, path)`` pairs for changed paths, optionally scoped."""
    try:
        # List individual untracked files rather than collapsed directories so
        # runtime lock paths can be filtered without hiding real board files
        # that happen to share their parent directory.
        arguments = ["status", "--porcelain", "--untracked-files=all"]
        if paths is not None:
            pathspecs = _literal_pathspecs(repository, paths)
            if not pathspecs:
                return []
            arguments.extend(("--", *pathspecs))
        result = run_git(repository, *arguments)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise GitCommandError("could not inspect local Git status") from error
    if result.returncode != 0:
        raise GitCommandError("could not inspect local Git status")
    return [
        (line[:2].strip(), line[3:].strip())
        for line in result.stdout.splitlines()
        if len(line) > 3 and line[3:].strip().replace("\\", "/") not in _RUNTIME_PATHS
    ]


def is_dirty(repository: Path, *, paths: Iterable[str | Path] | None = None) -> bool:
    """Return whether the repository has changed paths in the requested scope."""
    return bool(status(repository, paths=paths))


def add_all(repository: Path) -> bool:
    """Stage board content while excluding runtime-only synchronization files."""
    try:
        return ensure_runtime_ignored(repository) and run_git(repository, "add", "-A").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _add_paths(repository: Path, paths: Iterable[str | Path]) -> bool:
    """Stage only contained application-owned paths using literal pathspecs."""
    try:
        pathspecs = _literal_pathspecs(repository, paths)
        return bool(pathspecs) and ensure_runtime_ignored(repository) and run_git(
            repository, "add", "-A", "--", *pathspecs
        ).returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def move(repository: Path, source: Path, target: Path) -> bool:
    """Use ``git mv`` when possible, falling back to a filesystem rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if is_repo(repository):
        try:
            if run_git(repository, "mv", str(source), str(target)).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        source.replace(target)
    except OSError:
        return False
    return True


def commit(
    repository: Path,
    message: str,
    *,
    allow_empty: bool = False,
    paths: Iterable[str | Path] | None = None,
) -> bool:
    """Create a local commit, optionally limited to application-owned paths."""
    owned = tuple(paths) if paths is not None else None
    if not (_add_paths(repository, owned) if owned is not None else add_all(repository)):
        return False
    try:
        if not allow_empty and not is_dirty(repository, paths=owned):
            return False
    except GitCommandError:
        return False

    try:
        arguments: list[str] = []
        if not run_git(repository, "config", "user.name").stdout.strip():
            arguments += ["-c", "user.name=pykantui"]
        if not run_git(repository, "config", "user.email").stdout.strip():
            arguments += ["-c", "user.email=local@pykantui.invalid"]
        arguments += ["commit"]
        if owned is not None:
            arguments.append("--only")
        arguments += ["-m", message]
        if allow_empty:
            arguments.append("--allow-empty")
        if owned is not None:
            arguments.extend(("--", *_literal_pathspecs(repository, owned)))
        return run_git(repository, *arguments).returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
