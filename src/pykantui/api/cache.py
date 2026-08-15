"""Not asking the tracker things it already told us.

Three layers, cheapest first. Each one only runs when the one above it misses:

1. **Fresh cache hit — zero requests.** A response inside its TTL is returned
   from disk. Column layouts, project lists and member names change on a scale
   of weeks; refetching them on every sync is pure waste.
2. **Conditional request — one request, no body.** Past the TTL but holding an
   ``ETag`` or ``Last-Modified``, the request goes out with
   ``If-None-Match``/``If-Modified-Since``. A ``304`` costs a round trip and a
   header, not a payload, and on most APIs does not count against the rate
   limit the same way.
3. **Full request.** Only when there is nothing usable to revalidate against.

The cache lives in the user's pykantui state directory, scoped by provider,
project and local workspace::

    ~/.pykantui/cache/<provider>/<project-workspace>/<resource>.json

It never enters the project's local Git history.  Deleting it costs only the
next API request; the Markdown and sync baseline remain in the workspace.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from pykantui.config.paths import ensure_private_directory, write_text_atomic
from pykantui.core.naming import safe_name

from .types import JsonValue, QueryParams

#: How long each kind of response stays usable without asking again.
#:
#: The split is by how fast the thing actually changes, not by how big it is.
#: A board's columns change when someone redesigns the workflow -- a few times
#: a year. Issues change all day.
TTL_STRUCTURE = 6 * 60 * 60
"""Columns, projects, board configuration, member lists. Six hours."""

TTL_ISSUES = 60
"""Issue lists. A minute, so a burst of commands in one session shares a fetch."""

TTL_NONE = 0
"""Do not cache. The default, so caching is always opted into deliberately."""

SCHEMA = 1


@dataclass(frozen=True)
class CacheEntry:
    """One stored response, with whatever the server gave us to revalidate it."""

    body: JsonValue
    fetched_at: float
    etag: str = ""
    last_modified: str = ""

    def age(self) -> float:
        return max(0.0, time.time() - self.fetched_at)

    def is_fresh(self, ttl: float) -> bool:
        return ttl > 0 and self.age() < ttl

    def validators(self) -> dict[str, str]:
        """Conditional-request headers, where the server supplied any.

        Empty when it did not, in which case revalidating is impossible and a
        stale entry has to be refetched in full.
        """
        headers: dict[str, str] = {}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.last_modified:
            headers["If-Modified-Since"] = self.last_modified
        return headers


class ResponseCache:
    """A disk cache of API responses in the user's application state.

    Reads are memoised in process as well, so a run that asks for the same
    resource five times touches the disk once and the network not at all.
    """

    def __init__(self, root: Path, *, provider: str = "", project: str = "") -> None:
        self.root = root
        self.provider = safe_name(provider) if provider else ""
        self.project = safe_name(project) if project else ""
        self._memory: dict[tuple[str, str, str], CacheEntry] = {}

        #: Counters, so the effect is measurable rather than believed. The CLI
        #: prints these under ``--verbose`` and the tests assert on them.
        self.hits = 0
        self.revalidations = 0
        self.misses = 0

    # ---- placement -------------------------------------------------------

    def scope(self, provider: str, project: str) -> ResponseCache:
        """A view of this cache scoped to one provider and project."""
        scoped = ResponseCache(self.root, provider=provider, project=project)
        scoped._memory = self._memory
        return scoped

    def directory(self) -> Path:
        parts = [part for part in (self.provider, self.project) if part]
        return self.root.joinpath(*parts) if parts else self.root

    def path_for(self, key: str) -> Path:
        return self.directory() / f"{key}.json"

    def _memory_key(self, key: str) -> tuple[str, str, str]:
        """Scope an in-process key exactly as its on-disk directory is scoped."""
        return self.provider, self.project, key

    def _checked_path(self, path: Path) -> Path:
        """Reject cache reads and writes that traverse a symlink."""
        root = self.root.absolute()
        candidate = path.absolute()
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise OSError("cache path escaped its root") from error

        cursor = root
        if cursor.is_symlink():
            raise OSError("cache root is a symlink")
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise OSError("cache path traverses a symlink")
        try:
            candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        except (OSError, ValueError) as error:
            raise OSError("cache path escaped its resolved root") from error
        return path

    def _ensure_private_directory(self, directory: Path) -> None:
        """Create every cache scope with owner-only POSIX permissions."""

        root = self._checked_path(self.root)
        target = self._checked_path(directory)
        relative = target.relative_to(root)
        ensure_private_directory(root)
        cursor = root
        for part in relative.parts:
            cursor /= part
            ensure_private_directory(cursor)

    @staticmethod
    def key_for(method: str, url: str, params: QueryParams | None, label: str = "") -> str:
        """A stable, readable-ish filename for one request.

        The label goes in front so a human looking at the cache directory can
        tell ``columns-3f2a`` from ``issues-91bc``; the hash is what actually
        makes it unique, since two calls to the same path differ only by their
        query.
        """
        material = json.dumps(
            {"m": method.upper(), "u": url, "p": dict(sorted((params or {}).items()))},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
        return f"{safe_name(label)}-{digest}" if label else digest

    # ---- reading and writing --------------------------------------------

    def get(self, key: str) -> CacheEntry | None:
        memory_key = self._memory_key(key)
        cached = self._memory.get(memory_key)
        if cached is not None:
            return cached

        try:
            path = self._checked_path(self.path_for(key))
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            return None

        entry = CacheEntry(
            body=document.get("body"),
            fetched_at=float(document.get("fetched_at", 0.0)),
            etag=str(document.get("etag", "") or ""),
            last_modified=str(document.get("last_modified", "") or ""),
        )
        self._memory[memory_key] = entry
        return entry

    def put(self, key: str, body: JsonValue, *, etag: str = "", last_modified: str = "") -> None:
        entry = CacheEntry(body=body, fetched_at=time.time(), etag=etag, last_modified=last_modified)
        self._memory[self._memory_key(key)] = entry
        document = {
            "schema": SCHEMA,
            "fetched_at": entry.fetched_at,
            "etag": entry.etag,
            "last_modified": entry.last_modified,
            "body": body,
        }
        # A body that will not serialise, or a read-only disk. Neither is
        # worth failing a sync over -- the caller already has the response.
        with contextlib.suppress(OSError, TypeError):
            path = self._checked_path(self.path_for(key))
            self._ensure_private_directory(path.parent)
            write_text_atomic(path, json.dumps(document, ensure_ascii=False), private=True)

    def touch(self, key: str, entry: CacheEntry) -> None:
        """Record that a ``304`` confirmed an entry is still current."""
        self.put(key, entry.body, etag=entry.etag, last_modified=entry.last_modified)

    def discard(self, key: str) -> bool:
        """Remove one response from this scope without disturbing its peers."""

        removed = self._memory.pop(self._memory_key(key), None) is not None
        try:
            path = self._checked_path(self.path_for(key))
            if path.is_file():
                path.unlink()
                removed = True
        except OSError:
            pass
        return removed

    def clear(self) -> int:
        """Delete every cached response. Returns how many files went."""
        self._memory.clear()
        removed = 0
        try:
            root = self._checked_path(self.root)
        except OSError:
            return 0
        if not root.is_dir():
            return 0
        for path in sorted(root.rglob("*.json")):
            try:
                self._checked_path(path).unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def clear_label(self, label: str) -> int:
        """Delete one labelled resource while keeping this scope's others."""
        prefix = f"{safe_name(label)}-"
        for memory_key in [
            memory_key
            for memory_key in self._memory
            if memory_key[:2] == (self.provider, self.project) and memory_key[2].startswith(prefix)
        ]:
            self._memory.pop(memory_key, None)

        removed = 0
        try:
            directory = self._checked_path(self.directory())
        except OSError:
            return removed
        if not directory.is_dir():
            return removed
        for path in sorted(directory.glob(f"{prefix}*.json")):
            try:
                self._checked_path(path).unlink()
                removed += 1
            except OSError:
                continue
        return removed

    # ---- reporting -------------------------------------------------------

    def summary(self) -> str:
        total = self.hits + self.revalidations + self.misses
        if not total:
            return "no cacheable requests"
        return (
            f"{self.hits} from cache, {self.revalidations} revalidated, "
            f"{self.misses} fetched ({self.hits}/{total} avoided entirely)"
        )
