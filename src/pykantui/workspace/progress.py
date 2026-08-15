"""Provider-neutral, thread-safe observations of one workspace sync."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import ParamSpec, Protocol, TypeVar, cast


class SyncPhase(StrEnum):
    """Stable stages shown by every provider-backed sync client."""

    PREPARING = "preparing"
    APPLYING = "applying"
    FETCHING = "fetching"
    COMMENTS = "comments"
    RECONCILING = "reconciling"
    VERIFYING = "verifying"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    HELD = "held"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SyncProgressUpdate:
    """One immutable progress snapshot safe to pass across threads."""

    phase: SyncPhase
    completed: int = 0
    total: int | None = None
    item: str = ""
    summary: str = ""
    active: bool = True
    error: bool = False


SyncProgressCallback = Callable[[SyncProgressUpdate], None]


def emit_progress(
    callback: SyncProgressCallback | None,
    phase: SyncPhase,
    *,
    completed: int = 0,
    total: int | None = None,
    item: str = "",
    summary: str = "",
    active: bool = True,
    error: bool = False,
) -> SyncProgressUpdate:
    """Build and best-effort deliver one snapshot.

    Progress is observational. A broken UI callback must never change provider
    write safety or turn a successful sync into a failed one.
    """
    safe_total = _non_negative_count(total) if total is not None else None
    safe_completed = _non_negative_count(completed)
    if safe_total is not None:
        safe_completed = min(safe_completed, safe_total)
    update = SyncProgressUpdate(
        phase=phase,
        completed=safe_completed,
        total=safe_total,
        item=_one_line(item),
        summary=_one_line(summary),
        active=active,
        error=error,
    )
    if callback is not None:
        with suppress(Exception):  # observer failures cannot affect sync safety
            callback(update)
    return update


class ProgressCounter:
    """Count handled items in one determinate sync phase."""

    def __init__(
        self,
        callback: SyncProgressCallback | None,
        phase: SyncPhase,
        total: int,
        summary: str,
        *,
        announce_each: bool = True,
    ) -> None:
        self.callback = callback
        self.phase = phase
        self.total = max(0, total)
        self.summary = summary
        self.completed = 0
        self.announce_each = announce_each
        self.item = ""

    def before(self, item: str) -> None:
        self.item = item
        if self.completed and not self.announce_each:
            return
        emit_progress(
            self.callback,
            self.phase,
            completed=self.completed,
            total=self.total,
            item=item,
            summary=self.summary,
        )

    def after(self, item: str) -> None:
        self.item = item
        self.completed += 1
        emit_progress(
            self.callback,
            self.phase,
            completed=self.completed,
            total=self.total,
            item=item,
            summary=self.summary,
        )


_T = TypeVar("_T")


def collect_items(
    items: Iterable[_T],
    callback: SyncProgressCallback | None,
    phase: SyncPhase,
    label: Callable[[_T], str],
    summary: str,
) -> list[_T]:
    """Consume an unknown-length provider iterator with cumulative progress."""
    found: list[_T] = []
    emit_progress(callback, phase, completed=0, total=None, summary=summary)
    for item in items:
        found.append(item)
        emit_progress(
            callback,
            phase,
            completed=len(found),
            total=None,
            item=label(item),
            summary=summary,
        )
    return found


def tracked_items(
    items: Iterable[_T],
    counter: ProgressCounter | None,
    label: Callable[[_T], str],
) -> Iterable[_T]:
    """Yield work while advancing only after its loop body was handled."""
    for item in items:
        item_label = label(item)
        if counter is not None:
            counter.before(item_label)
        yield item
        if counter is not None:
            counter.after(item_label)


class _SyncReport(Protocol):
    held: list[str]
    skipped: list[tuple[str, str]]

    def summary(self) -> str: ...


_P = ParamSpec("_P")
_R = TypeVar("_R", bound=_SyncReport)


def report_sync_progress(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Emit terminal progress around a complete, lock-protected sync."""

    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        callback = cast(SyncProgressCallback | None, kwargs.get("progress"))
        latest = SyncProgressUpdate(phase=SyncPhase.PREPARING)

        def observe(update: SyncProgressUpdate) -> None:
            nonlocal latest
            latest = update
            if callback is not None:
                callback(update)

        mutable_kwargs = cast(dict[str, object], kwargs)
        mutable_kwargs["progress"] = observe
        emit_progress(observe, SyncPhase.PREPARING, summary="Checking local Markdown and provider state")
        try:
            report = function(*args, **kwargs)
        except Exception as error:
            emit_progress(
                callback,
                SyncPhase.FAILED,
                completed=latest.completed,
                total=latest.total,
                item=latest.item,
                summary=str(error).splitlines()[0],
                active=False,
                error=True,
            )
            raise
        phase = SyncPhase.HELD if report.held or report.skipped else SyncPhase.COMPLETE
        emit_progress(
            callback,
            phase,
            completed=latest.completed,
            total=latest.total,
            item=latest.item,
            summary=report.summary(),
            active=False,
        )
        return report

    return wrapped


def _one_line(value: str, limit: int = 160) -> str:
    terminal_safe = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character
        for character in str(value)
    )
    text = " ".join(terminal_safe.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _non_negative_count(value: object) -> int:
    """Fail closed when a plugin supplies a malformed runtime counter."""
    return max(0, value) if type(value) is int else 0


__all__ = [
    "ProgressCounter",
    "SyncPhase",
    "SyncProgressCallback",
    "SyncProgressUpdate",
    "collect_items",
    "emit_progress",
    "report_sync_progress",
    "tracked_items",
]
