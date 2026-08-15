"""Where board data lives on disk, and how it gets written there safely."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path


def data_dir() -> Path:
    """The user's pykantui state directory.

    ``PYKANTUI_HOME`` wins, so tests and demos never touch a real board.
    A regular pip install uses ``~/.pykantui`` on every operating system.  A
    single predictable location matters here because it is also the index of
    provider workspaces the user deliberately placed elsewhere on disk.
    """
    override = os.getenv("PYKANTUI_HOME")
    if override:
        return Path(override).expanduser().resolve()

    return Path.home() / ".pykantui"


def board_path(name: str = "board") -> Path:
    return data_dir() / f"{name}.json"


def config_path() -> Path:
    """The board shape: columns, their order, and what they mean."""
    return data_dir() / "config.json"


def auth_path() -> Path:
    """Provider credentials, outside every committable workspace."""
    return data_dir() / "auth.json"


def cache_path() -> Path:
    """Global provider-response cache, outside every project workspace."""

    return data_dir() / "cache"


def projects_path() -> Path:
    """Registry linking provider projects to user-selected workspace paths."""

    return data_dir() / "projects.json"


def ensure_private_directory(path: Path) -> None:
    """Create an application-data directory with owner-only POSIX access.

    Windows uses inherited ACLs; ``chmod`` there only changes the read-only
    attribute and would provide a false security guarantee.
    """

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def ensure_private_file(path: Path) -> None:
    """Repair owner-only POSIX access on an existing private file."""

    if os.name != "nt":
        path.chmod(0o600)


def migrate_legacy_data(
    *,
    sources: Iterable[Path] | None = None,
    destination: Path | None = None,
) -> list[Path]:
    """Copy top-level JSON state from the former platform-specific location.

    Existing files always win, caches are intentionally not copied, and the
    old directory is never changed.  Explicit arguments make the migration
    deterministic in tests; an explicit ``PYKANTUI_HOME`` disables automatic
    migration for containers and portable installs.
    """

    if sources is None:
        if os.getenv("PYKANTUI_HOME"):
            return []
        sources = _legacy_data_dirs()
    target_root = destination or data_dir()
    migrated: list[Path] = []
    for source_root in sources:
        try:
            if source_root.resolve(strict=False) == target_root.resolve(strict=False):
                continue
        except OSError:
            continue
        for source in sorted(source_root.glob("*.json")):
            target = target_root / source.name
            if target.exists() or source.is_symlink() or not source.is_file():
                continue
            try:
                text = source.read_text(encoding="utf-8")
                write_text_atomic(target, text, private=source.name == "auth.json")
            except OSError:
                continue
            migrated.append(target)
    return migrated


def _legacy_data_dirs() -> tuple[Path, ...]:
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return (Path(base) / "pykantui",)
    base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return (Path(base) / "pykantui",)


def write_text_atomic(target: Path, text: str, *, private: bool = False) -> None:
    """Replace ``target``'s contents in one step, or leave the old file intact.

    A plain ``write_text`` truncates before it writes, so an interrupted save
    leaves a half-written file behind. The board is saved on every single
    mutation, which makes that a real way to lose one.

    ``private`` restricts the file to its owner when it carries provider
    credentials. Windows ACLs are not covered -- ``chmod`` there only toggles the
    read-only bit -- so that part is a POSIX guarantee only.
    """
    if private:
        ensure_private_directory(target.parent)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.{os.getpid()}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    # Keep the caller's concrete path flavour. Tests legitimately exercise the
    # POSIX permission branch on Windows by patching ``os.name``; constructing
    # a fresh ``Path`` there would incorrectly try to create a ``PosixPath``.
    temporary = type(target)(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        if private and os.name != "nt":
            temporary.chmod(0o600)
        # Atomic on POSIX, and on Windows for a same-volume rename. The temp
        # file is a sibling of the target, so that always holds.
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
