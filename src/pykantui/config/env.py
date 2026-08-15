"""Reading a ``.env`` file, without a dependency for it.

Credentials reach a provider through environment variables -- ``JIRA_TOKEN``,
``PLANE_TOKEN`` and so on. Keeping them in a gitignored ``.env`` beside the
project is the ordinary way to do that, and a tool that ignores the file it
told you to create is a tool that makes you export things by hand.

A small parser rather than ``python-dotenv``, because this is all that is
needed: ``KEY=value``, comments, blank lines, and optional surrounding quotes.
Anything more elaborate belongs in the shell that launched us.

**Real environment variables always win.** A ``.env`` is a convenience for the
common case; an explicitly exported variable is a deliberate act, and it should
not be silently overridden by a file the user forgot was there.
"""

from __future__ import annotations

import os
from pathlib import Path

FILENAME = ".env"
_LOADED_VALUES: dict[str, str] = {}


def load(start: Path | None = None, *, depth: int = 3) -> Path | None:
    """Load the nearest ``.env`` at or above ``start``. Returns the file used.

    Walks up a little way, so running ``kbn`` from a subdirectory of a project
    still finds the project's file -- the same reason git looks upward for its
    config. Bounded, so a stray ``.env`` in a home directory does not leak into
    an unrelated project three levels down.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *list(here.parents)[:depth]):
        path = candidate / FILENAME
        if path.is_file():
            apply(read(path))
            return path
    return None


def read(path: Path) -> dict[str, str]:
    """Parse a ``.env`` into a mapping. Unreadable or malformed lines are skipped."""
    found: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        # `export FOO=bar` is common in files people also source from a shell.
        name = name.removeprefix("export ").strip()
        found[name] = _unquote(_without_comment(value.strip()))
    return found


def apply(values: dict[str, str]) -> None:
    """Apply documented provider values without changing process controls.

    A workspace ``.env`` is untrusted project input.  Only names declared by a
    provider contract may enter the process; accepting arbitrary keys would
    let a checked-out workspace redirect HTTP through a proxy, replace the TLS
    trust store, or move pykantui's private data directory.
    """
    allowed = _provider_environment_names()
    for name, value in values.items():
        if name not in allowed:
            continue
        if name not in os.environ:
            os.environ[name] = value
            _LOADED_VALUES[name] = value


def supplied_by_file(name: str, value: str) -> bool:
    """Return whether this exact process value came from the loaded ``.env``.

    The value comparison matters: callers may replace an environment variable
    after startup, in which case it is an explicit process value again.  This
    provenance contains no credential material beyond what is already present
    in the current process and is never persisted or logged.
    """

    return bool(value) and os.environ.get(name) == value and _LOADED_VALUES.get(name) == value


def _provider_environment_names() -> frozenset[str]:
    """Environment names explicitly declared by installed provider specs."""
    from pykantui.tracker import specs  # noqa: PLC0415 - provider discovery stays lazy

    return frozenset(
        name
        for spec in specs(available_only=False)
        for field in (*spec.auth_fields, *spec.config_fields)
        for name in field.env_vars
    )


def _without_comment(value: str) -> str:
    """Drop a trailing ``# ...`` note from an unquoted value.

    People annotate these files -- ``PLANE_BASE_URL=   # optional`` is the obvious
    thing to write -- and without this the value becomes the literal string
    "# optional", which then arrives as a base URL and fails somewhere far away
    from the file that caused it.

    Only when the ``#`` follows whitespace, and never inside quotes: a token is
    allowed to contain a ``#``, and truncating one would be a worse bug than
    the one this fixes.
    """
    if value[:1] in ("'", '"'):
        return value

    if value.startswith("#"):
        return ""  # the whole value is a note: the field is simply unset

    marker = value.find(" #")
    return value if marker == -1 else value[:marker].rstrip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value
