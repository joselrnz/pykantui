"""Cross-platform exclusion for workspace mutations.

The lock file remains on disk; the operating-system lock, not its presence,
is authoritative. Keeping the inode avoids a delete-and-recreate race between
three processes entering the same workspace.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Concatenate, ParamSpec, TypeVar, cast

from filelock import FileLock, Timeout

from pykantui.api import ProviderError
from pykantui.config.paths import write_text_atomic
from pykantui.workspace import layout
from pykantui.workspace.paths import ensure_workspace_path

_P = ParamSpec("_P")
_R = TypeVar("_R")


@dataclass(frozen=True, slots=True)
class LockOwner:
    """Diagnostic identity for the process holding a workspace lock."""

    pid: int
    host: str
    started_at: str

    @classmethod
    def current(cls) -> LockOwner:
        """Describe the current process at lock-acquisition time."""
        return cls(
            pid=os.getpid(),
            host=socket.gethostname() or "unknown",
            started_at=datetime.now(UTC).isoformat(),
        )


@contextmanager
def exclusive_workspace(workspace: Path) -> Iterator[None]:
    """Prevent two processes from mutating one workspace concurrently."""
    lock_path = ensure_workspace_path(workspace, layout.meta_dir(workspace) / "sync.lock")
    owner_path = ensure_workspace_path(workspace, layout.meta_dir(workspace) / "sync.lock.owner.json")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path, timeout=0)
    try:
        with lock:
            _write_owner(owner_path, LockOwner.current())
            try:
                yield
            finally:
                with suppress(OSError, ProviderError):
                    ensure_workspace_path(workspace, owner_path).unlink(missing_ok=True)
    except Timeout as error:
        owner = _read_owner(owner_path)
        owner_hint = (
            f" The active sync is process {owner.pid} on {owner.host}, started {owner.started_at}."
            if owner is not None
            else ""
        )
        raise ProviderError(
            f"already syncing {workspace}",
            hint=f"Wait for the other sync to finish, then try again.{owner_hint}",
        ) from error


def _write_owner(path: Path, owner: LockOwner) -> None:
    """Atomically replace stale owner metadata after acquiring the OS lock."""
    write_text_atomic(path, json.dumps(asdict(owner), indent=2))


def _read_owner(path: Path) -> LockOwner | None:
    """Read best-effort diagnostics without making lock contention fail."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return LockOwner(
            pid=int(document["pid"]),
            host=str(document["host"]),
            started_at=str(document["started_at"]),
        )
    except (KeyError, OSError, TypeError, ValueError):
        return None


def with_workspace_lock(
    function: Callable[Concatenate[Path, _P], _R],
) -> Callable[Concatenate[Path, _P], _R]:
    """Serialize a workspace-mutating function by its first argument."""

    @wraps(function)
    def wrapped(workspace: Path, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with exclusive_workspace(workspace):
            return function(workspace, *args, **kwargs)

    return cast(Callable[Concatenate[Path, _P], _R], wrapped)
