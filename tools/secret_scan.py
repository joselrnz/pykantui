"""Deterministic, dependency-free repository secret scanner.

The release gate intentionally reports only a relative path and a finding
category.  It never prints matched text, which keeps the scanner itself from
copying a credential into CI logs.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from re import Pattern

_SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "artifacts",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
    }
)
_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".avi",
        ".bmp",
        ".class",
        ".dll",
        ".dylib",
        ".eot",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mo",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".pyc",
        ".pyo",
        ".so",
        ".tar",
        ".ttf",
        ".wav",
        ".webm",
        ".woff",
        ".woff2",
        ".xz",
        ".zip",
    }
)
_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("github-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{70,255}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,255}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{40,255}\b")),
    ("stripe-live-key", re.compile(r"\bsk_live_[A-Za-z0-9]{20,255}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,255}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("linear-token", re.compile(r"\blin_api_[A-Za-z0-9_-]{30,255}\b")),
    ("clickup-token", re.compile(r"\bpk_[0-9]{4,20}_[A-Za-z0-9_-]{20,255}\b")),
    ("plane-token", re.compile(r"\bplane_api_[A-Za-z0-9_-]{20,255}\b")),
    ("atlassian-token", re.compile(r"\bATATT[A-Za-z0-9_-]{20,255}\b")),
    ("trello-token", re.compile(r"\bATTA[A-Za-z0-9]{40,255}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)
_SENSITIVE_ENV_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASS|API_KEY|PRIVATE_KEY|CREDENTIAL|AUTH)(?:$|_)",
    re.IGNORECASE,
)
_PATH_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_PLACEHOLDERS = frozenset(
    {
        "changeme",
        "change-me",
        "dummy",
        "example",
        "none",
        "null",
        "password",
        "replace-me",
        "secret",
        "test-token",
        "token",
        "your-token",
    }
)


@dataclass(frozen=True, order=True, slots=True)
class Finding:
    """A sanitized secret finding suitable for terminal and CI output."""

    path: str
    category: str


def _is_skipped(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return any(part in _SKIPPED_DIRECTORIES for part in relative.parts[:-1])


def _git_candidates(root: Path) -> tuple[Path, ...] | None:
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    names = completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    paths = (root / name for name in names if name)
    return tuple(
        sorted(
            (
                path
                for path in paths
                if path.is_file() and not _is_skipped(path, root)
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )


def _filesystem_candidates(root: Path) -> tuple[Path, ...]:
    discovered: list[Path] = []
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in _SKIPPED_DIRECTORIES)
        base = Path(directory)
        for name in sorted(files):
            path = base / name
            discovered.append(path)
    return tuple(sorted(discovered, key=lambda path: path.relative_to(root).as_posix()))


def candidate_files(root: Path) -> tuple[Path, ...]:
    """Return tracked and unignored files, or a deterministic tree fallback."""

    resolved = root.resolve()
    git_paths = _git_candidates(resolved)
    return git_paths if git_paths is not None else _filesystem_candidates(resolved)


def _looks_binary(path: Path) -> bool:
    if path.suffix.casefold() in _BINARY_SUFFIXES:
        return True
    try:
        with path.open("rb") as stream:
            sample = stream.read(8192)
    except OSError:
        return False
    if b"\0" in sample:
        return True
    if not sample:
        return False
    control_count = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return control_count / len(sample) > 0.10


def _read_text(path: Path) -> str | None:
    if _looks_binary(path):
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _dotenv_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] in {'"', "'"}:
        quote = value[0]
        closing = value.find(quote, 1)
        if closing >= 1:
            return value[1:closing]
    comment = value.find(" #")
    return (value[:comment] if comment >= 0 else value).strip()


def _is_nontrivial_secret(value: str) -> bool:
    normalized = value.strip().casefold()
    if len(value) < 8 or normalized in _PLACEHOLDERS or "..." in value:
        return False
    if normalized.startswith(("your-", "your_", "replace-", "replace_", "example-", "example_")):
        return False
    return not value.isdecimal() and len(set(value)) >= 4


def _local_secret_values(root: Path) -> frozenset[str]:
    values: set[str] = set()
    for path in sorted(root.rglob(".env*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name == ".env.example" or _is_skipped(path, root):
            continue
        text = _read_text(path)
        if text is None:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, separator, raw_value = line.partition("=")
            if separator and _SENSITIVE_ENV_NAME.search(name.strip()):
                value = _dotenv_value(raw_value)
                if _is_nontrivial_secret(value):
                    values.add(value)
    return frozenset(values)


def _find_categories(text: str, local_values: Iterable[str]) -> set[str]:
    categories = {category for category, pattern in _PATTERNS if pattern.search(text)}
    if any(value in text for value in local_values):
        categories.add("local-env-secret")
    return categories


def _diagnostic_path(path: Path, root: Path, local_values: Iterable[str]) -> str:
    relative = path.relative_to(root).as_posix()
    for value in sorted(local_values, key=len, reverse=True):
        relative = relative.replace(value, "[REDACTED]")
    for _, pattern in _PATTERNS:
        relative = pattern.sub("[REDACTED]", relative)
    return _PATH_CONTROL.sub("?", relative)


def scan_repository(root: Path) -> tuple[Finding, ...]:
    """Scan one repository without returning or printing matched values."""

    resolved = root.resolve()
    local_values = _local_secret_values(resolved)
    findings: set[Finding] = set()
    for path in candidate_files(resolved):
        text = _read_text(path)
        if text is None:
            continue
        relative = _diagnostic_path(path, resolved, local_values)
        findings.update(Finding(path=relative, category=category) for category in _find_categories(text, local_values))
    return tuple(sorted(findings))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the release gate and emit only sanitized path/category diagnostics."""

    parser = argparse.ArgumentParser(description="Scan repository files for credentials")
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    findings = scan_repository(args.root)
    for finding in findings:
        print(f"{finding.path}: {finding.category}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
