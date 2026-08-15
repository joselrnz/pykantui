"""Containment checks for every mutable workspace path."""

from __future__ import annotations

from pathlib import Path

from pykantui.api import ProviderError


def ensure_workspace_path(workspace: Path, path: Path) -> Path:
    """Return ``path`` only when it cannot escape through names or symlinks.

    The check is intentionally performed immediately before each filesystem
    operation. Provider-controlled project and column names are sanitized, but
    a local process can still replace one of their directories with a symlink
    between syncs.
    """
    workspace_absolute = workspace.absolute()
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(workspace_absolute)
    except ValueError as error:
        raise _unsafe(workspace, path) from error

    cursor = workspace_absolute
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise _unsafe(workspace, path)

    try:
        candidate.resolve(strict=False).relative_to(workspace.resolve(strict=False))
    except (OSError, ValueError) as error:
        raise _unsafe(workspace, path) from error
    return path


def _unsafe(workspace: Path, path: Path) -> ProviderError:
    return ProviderError(
        f"refusing workspace path outside {workspace}: {path}",
        hint="Remove the symlink or choose a path inside this workspace, then retry.",
    )


__all__ = ["ensure_workspace_path"]
