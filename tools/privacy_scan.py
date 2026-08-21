"""Scan publishable repository files for personal identity exposure.

The scanner reports only relative paths and finding categories. Denied values
are supplied at invocation time so a private name, account handle, or email
never has to be committed to the repository to enforce the gate.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from tools.secret_scan import candidate_files

_USERS_DIRECTORY = "Users"
_HOME_DIRECTORY = "home"
_HOME_PATH = re.compile(
    rf"(?:[A-Za-z]:[\\/]{_USERS_DIRECTORY}[\\/](?P<windows_user>[^\\/\s]+)"
    rf"|/(?P<unix_root>{_USERS_DIRECTORY}|{_HOME_DIRECTORY})/(?P<unix_user>[^/\s]+))"
)
_PUBLIC_SERVICE_HOME_USERS = frozenset({"kbn"})
_TEXT_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".dll",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".mo",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".tar",
        ".ttf",
        ".wav",
        ".webm",
        ".woff",
        ".woff2",
        ".zip",
    }
)
_IMAGE_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})


@dataclass(frozen=True, order=True, slots=True)
class PrivacyFinding:
    """A redacted privacy finding safe to print in a build log."""

    path: str
    category: str


def _normalized_denials(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({value.strip().casefold() for value in values if value.strip()}, key=len, reverse=True))


def _contains_denied(text: str, denied: Sequence[str]) -> bool:
    folded = text.casefold()
    return any(value in folded for value in denied)


def _safe_path(path: Path, root: Path, denied: Sequence[str]) -> str:
    relative = path.relative_to(root).as_posix()
    for value in denied:
        relative = re.sub(re.escape(value), "[REDACTED]", relative, flags=re.IGNORECASE)
    return _HOME_PATH.sub("[REDACTED-HOME]", relative)


def _has_private_home_path(text: str) -> bool:
    for match in _HOME_PATH.finditer(text):
        if match.group("unix_root") == _HOME_DIRECTORY and match.group("unix_user") in _PUBLIC_SERVICE_HOME_USERS:
            continue
        return True
    return False


def _read_text(path: Path) -> str | None:
    if path.suffix.casefold() in _TEXT_BINARY_SUFFIXES:
        return None
    try:
        sample = path.read_bytes()
    except OSError:
        return None
    if b"\0" in sample[:8192]:
        return None
    return sample.decode("utf-8", errors="replace")


def _metadata_text(path: Path) -> str | None:
    if path.suffix.casefold() not in _IMAGE_SUFFIXES:
        return None
    try:
        with Image.open(path) as image:
            return "\n".join(f"{key}={value}" for key, value in sorted(image.info.items()) if isinstance(value, str))
    except (OSError, UnidentifiedImageError):
        return None


def scan_repository(root: Path, *, denied: Sequence[str] = ()) -> tuple[PrivacyFinding, ...]:
    """Scan tracked and unignored files without returning matched values."""

    resolved = root.resolve()
    normalized = _normalized_denials(denied)
    findings: set[PrivacyFinding] = set()
    for path in candidate_files(resolved):
        diagnostic = _safe_path(path, resolved, normalized)
        text = _read_text(path)
        if text is not None:
            if _has_private_home_path(text):
                findings.add(PrivacyFinding(path=diagnostic, category="absolute-home-path"))
            if normalized and _contains_denied(text, normalized):
                findings.add(PrivacyFinding(path=diagnostic, category="denied-identity"))
        metadata = _metadata_text(path)
        if metadata is not None:
            if _has_private_home_path(metadata):
                findings.add(PrivacyFinding(path=diagnostic, category="absolute-home-image-metadata"))
            if normalized and _contains_denied(metadata, normalized):
                findings.add(PrivacyFinding(path=diagnostic, category="denied-image-metadata"))
    return tuple(sorted(findings))


def _environment_denials() -> tuple[str, ...]:
    return tuple(value for value in os.environ.get("PYKANTUI_PRIVACY_DENY", "").split(os.pathsep) if value)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the release privacy gate with sanitized diagnostics."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--deny", action="append", default=[], help="identity value to reject (repeatable)")
    arguments = parser.parse_args(argv)
    findings = scan_repository(arguments.root, denied=(*_environment_denials(), *arguments.deny))
    for finding in findings:
        print(f"{finding.path}: {finding.category}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
