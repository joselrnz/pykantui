"""Fail-closed foundation for sequential live-provider certification.

The module contains no provider mutation implementation.  A certification
driver supplies one single-shot sender and one direct readback callback at a
time.  The command-line interface is therefore a dry-run context validator;
``--execute`` only unlocks the API after the exact environment gate is also
present.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from filelock import FileLock

LIVE_WRITE_ENV = "PYKANTUI_LIVE_WRITES"
_RUN_TAG = re.compile(r"^PKT-E2E-\d{8}T\d{6}Z-[0-9a-f]{8}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9-]*$")
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:token|secret|password|pass|api_key|private_key|credential|authorization|auth)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_|github_pat_|glpat-|lin_api_|plane_api_|ATATT|ATTA|sk_live_|sk-(?:proj-)?|xox[baprs]-)"
    r"[A-Za-z0-9_.-]{16,}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b)"
)


class CertificationError(RuntimeError):
    """Base class for certification failures that must stop the lane."""


class WritesDisabledError(CertificationError):
    """The explicit flag and environment interlock were not both present."""


class OwnershipError(CertificationError):
    """A project, title, or readback record is not owned by this run."""


class ReplayBlockedError(CertificationError):
    """A single-shot operation was already attempted in this receipt log."""


class AmbiguousWriteError(CertificationError):
    """A write may have reached the provider and must never be replayed."""


def generate_run_tag(*, now: datetime | None = None, nonce: str | None = None) -> str:
    """Return the exact stable ownership marker used by every provider."""

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    suffix = (nonce or secrets.token_hex(4)).lower()
    if not re.fullmatch(r"[0-9a-f]{8}", suffix):
        raise ValueError("run-tag nonce must be exactly eight lowercase hexadecimal characters")
    return f"PKT-E2E-{moment:%Y%m%dT%H%M%SZ}-{suffix}"


def owned_title(run_tag: str, provider: str, phase: str = "create") -> str:
    """Return the only title considered owned by a certification create."""

    normalized = provider.strip().lower()
    normalized_phase = phase.strip().lower()
    if (
        not _RUN_TAG.fullmatch(run_tag)
        or not _PROVIDER.fullmatch(normalized)
        or not re.fullmatch(r"[a-z][a-z0-9-]*", normalized_phase)
    ):
        raise ValueError("invalid certification run tag or provider name")
    return f"[{run_tag}:{normalized}] {normalized_phase}"


@dataclass(frozen=True, slots=True)
class CertificationContext:
    """Exact provider/project/run identity required before any mutation."""

    provider: str
    expected_project_id: str
    actual_project_id: str
    run_tag: str

    def __post_init__(self) -> None:
        normalized = self.provider.strip().lower()
        object.__setattr__(self, "provider", normalized)
        if not _PROVIDER.fullmatch(normalized):
            raise OwnershipError("provider name is not canonical")
        if not _RUN_TAG.fullmatch(self.run_tag):
            raise OwnershipError("run tag is not canonical")
        if not self.expected_project_id or self.actual_project_id != self.expected_project_id:
            raise OwnershipError("live provider project does not exactly match the expected project id")

    @property
    def title(self) -> str:
        return owned_title(self.run_tag, self.provider)

    def assert_owned(self, *, project_id: str, title: str, expected_title: str | None = None) -> None:
        """Reject records not carrying both exact project and title identity."""

        if project_id != self.expected_project_id or title != (expected_title or self.title):
            raise OwnershipError("remote record is not exactly owned by this certification run")


@dataclass(frozen=True, slots=True)
class MutationRecord:
    """Minimal canonical identity returned by a mutation or direct readback."""

    remote_id: str
    project_id: str
    title: str


@dataclass(frozen=True, slots=True)
class ReadbackRequest:
    """A required provider-direct read request; cache bypass is non-optional."""

    project_id: str
    remote_id: str
    bypass_cache: bool = True


@dataclass(frozen=True, slots=True)
class CertificationResult:
    """One dry-run or verified single-shot result."""

    dry_run: bool
    operation: str
    operation_id: str
    record: MutationRecord | None = None


class ReceiptLog:
    """Append-only, process-safe JSONL receipts with recursive redaction."""

    def __init__(self, path: Path, *, sensitive_values: Sequence[str] = ()) -> None:
        self.path = path
        self._lock = FileLock(f"{path}.lock")
        self._sensitive_values = tuple(value for value in sensitive_values if value)

    def append(
        self,
        *,
        event: str,
        context: CertificationContext,
        operation: str,
        operation_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one complete UTF-8 line under an adjacent interprocess lock."""

        _validate_receipt_identity(event, operation, operation_id, context, self._sensitive_values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            sequence = self._next_sequence()
            record: dict[str, Any] = {
                "schema": 1,
                "sequence": sequence,
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "provider": context.provider,
                "project_id": context.expected_project_id,
                "run_tag": context.run_tag,
                "operation": operation,
                "operation_id": operation_id,
            }
            cleaned = _sanitize(details or {}, self._sensitive_values)
            if cleaned:
                record["details"] = cleaned
            encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise OSError("receipt append was incomplete")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def was_attempted(
        self, context: CertificationContext, operation: str, operation_id: str
    ) -> bool:
        """Return whether this exact operation has a durable attempted receipt."""

        if not self.path.is_file():
            return False
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return True
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return True
            if (
                record.get("event") == "attempted"
                and record.get("provider") == context.provider
                and record.get("project_id") == context.expected_project_id
                and record.get("run_tag") == context.run_tag
                and record.get("operation") == operation
                and record.get("operation_id") == operation_id
            ):
                return True
        return False

    def _next_sequence(self) -> int:
        if not self.path.is_file():
            return 1
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 1
        for line in reversed(lines):
            try:
                return int(json.loads(line).get("sequence", 0)) + 1
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return 1


class CertificationRunner:
    """Execute caller-supplied mutations through the certification interlocks."""

    def __init__(
        self,
        context: CertificationContext,
        receipts: ReceiptLog,
        *,
        execute: bool = False,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.context = context
        self.receipts = receipts
        self.execute = execute
        self.environment = os.environ if environment is None else environment
        self._attempted: set[tuple[str, str]] = set()

    def require_writes_enabled(self) -> None:
        if not self.execute or self.environment.get(LIVE_WRITE_ENV) != "1":
            raise WritesDisabledError(
                "live writes require both --execute and PYKANTUI_LIVE_WRITES=1"
            )

    def run_single_shot(
        self,
        operation: str,
        operation_id: str,
        send: Callable[[], MutationRecord],
        readback: Callable[[ReadbackRequest], MutationRecord | None],
        *,
        expected_title: str | None = None,
    ) -> CertificationResult:
        """Send once, never replay, then require exact uncached direct readback."""

        if not self.execute:
            return CertificationResult(True, operation, operation_id)
        self.require_writes_enabled()
        key = (operation, operation_id)
        if key in self._attempted or self.receipts.was_attempted(
            self.context, operation, operation_id
        ):
            raise ReplayBlockedError("operation already has an attempted receipt")
        self._attempted.add(key)
        self.receipts.append(
            event="attempted",
            context=self.context,
            operation=operation,
            operation_id=operation_id,
        )
        try:
            created = send()
        except Exception as error:
            self.receipts.append(
                event="ambiguous",
                context=self.context,
                operation=operation,
                operation_id=operation_id,
                details={"error_type": type(error).__name__},
            )
            raise AmbiguousWriteError(
                "provider write outcome is ambiguous; direct readback is required and replay is blocked"
            ) from error

        wanted_title = expected_title or self.context.title
        self.context.assert_owned(
            project_id=created.project_id,
            title=created.title,
            expected_title=wanted_title,
        )
        if not created.remote_id:
            raise AmbiguousWriteError("provider accepted the write without a canonical remote id")
        self.receipts.append(
            event="accepted",
            context=self.context,
            operation=operation,
            operation_id=operation_id,
            details={"remote_id": created.remote_id},
        )
        request = ReadbackRequest(
            project_id=self.context.expected_project_id,
            remote_id=created.remote_id,
        )
        try:
            confirmed = readback(request)
        except Exception as error:
            self.receipts.append(
                event="readback-failed",
                context=self.context,
                operation=operation,
                operation_id=operation_id,
                details={"error_type": type(error).__name__, "remote_id": created.remote_id},
            )
            raise AmbiguousWriteError(
                "accepted write could not be verified; replay is blocked"
            ) from error
        if confirmed is None:
            self.receipts.append(
                event="readback-missing",
                context=self.context,
                operation=operation,
                operation_id=operation_id,
                details={"remote_id": created.remote_id},
            )
            raise AmbiguousWriteError("direct uncached readback did not find the accepted record")
        self.context.assert_owned(
            project_id=confirmed.project_id,
            title=confirmed.title,
            expected_title=wanted_title,
        )
        if confirmed.remote_id != created.remote_id:
            raise OwnershipError("direct readback returned a different canonical id")
        self.receipts.append(
            event="verified",
            context=self.context,
            operation=operation,
            operation_id=operation_id,
            details={"remote_id": confirmed.remote_id, "bypass_cache": request.bypass_cache},
        )
        return CertificationResult(False, operation, operation_id, confirmed)


def _sanitize(value: Any, sensitive_values: Sequence[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item, sensitive_values)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, sensitive_values) for item in value]
    if isinstance(value, Path):
        return "<redacted-path>" if value.is_absolute() else value.as_posix()
    if isinstance(value, str):
        if any(secret in value for secret in sensitive_values) or _SECRET_VALUE.search(value):
            return "<redacted>"
        if _is_absolute_path(value):
            return "<redacted-path>"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def _validate_receipt_identity(
    event: str,
    operation: str,
    operation_id: str,
    context: CertificationContext,
    sensitive_values: Sequence[str],
) -> None:
    for label, value in (
        ("event", event),
        ("operation", operation),
    ):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
            raise ValueError(f"receipt {label} is not canonical")
    for label, value in (
        ("operation id", operation_id),
        ("project id", context.expected_project_id),
    ):
        if (
            not value
            or _is_absolute_path(value)
            or _SECRET_VALUE.search(value)
            or any(secret in value for secret in sensitive_values)
        ):
            raise ValueError(f"receipt {label} is unsafe")


def _is_absolute_path(value: str) -> bool:
    if "://" in value:
        return False
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--expected-project-id", required=True)
    parser.add_argument("--actual-project-id", required=True)
    parser.add_argument("--run-tag", default="")
    parser.add_argument("--receipts", type=Path, default=Path("artifacts/live-certification/receipts.jsonl"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    run_tag = args.run_tag or generate_run_tag()
    context = CertificationContext(
        provider=args.provider,
        expected_project_id=args.expected_project_id,
        actual_project_id=args.actual_project_id,
        run_tag=run_tag,
    )
    runner = CertificationRunner(context, ReceiptLog(args.receipts), execute=args.execute)
    if args.execute:
        runner.require_writes_enabled()
    print(
        json.dumps(
            {
                "mode": "armed" if args.execute else "dry-run",
                "provider": context.provider,
                "project_id": context.expected_project_id,
                "run_tag": context.run_tag,
                "title": context.title,
            },
            separators=(",", ":"),
        )
    )
    return 0


def entrypoint() -> int:
    """Render fail-closed CLI errors without paths, payloads, or tracebacks."""

    try:
        return main()
    except (CertificationError, ValueError) as error:
        print(f"certification blocked: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(entrypoint())
