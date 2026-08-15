"""Safe subprocess boundary for the local Git executable."""

from __future__ import annotations

import subprocess
from pathlib import Path

#: Long enough for a slow index refresh, while bounding a stuck Git process.
DEFAULT_TIMEOUT_SECONDS = 60.0
VERSION_TIMEOUT_SECONDS = 10.0


class GitCommandError(RuntimeError):
    """A local Git safety check failed and must not be treated as clean state."""


def available() -> bool:
    """Return whether a usable ``git`` executable is on ``PATH``."""
    try:
        result = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def run_git(
    repository: Path,
    *arguments: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run one Git command without a shell and capture its output."""
    return subprocess.run(  # noqa: S603 - argv is explicit; no shell interpolation
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
