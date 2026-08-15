"""Sanitize untrusted provider text before it reaches a terminal or log."""

from __future__ import annotations

import re
from collections.abc import Iterable

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MARKUP = re.compile(r"\[/?[A-Za-z][^\]\r\n]{0,48}\]")
_TOKEN_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}"),
    re.compile(r"\bglpat-[A-Za-z0-9._-]{8,}"),
)


def redact(text: object, sensitive_values: Iterable[str] = ()) -> str:
    """Return one terminal-safe line with known and recognizable tokens hidden."""

    cleaned = _CONTROL.sub(" ", str(text)).replace("\r", " ").replace("\n", " ")
    cleaned = _MARKUP.sub(lambda match: match.group(0).replace("[", "‹").replace("]", "›"), cleaned)
    for value in sorted({item for item in sensitive_values if len(item) >= 4}, key=len, reverse=True):
        cleaned = cleaned.replace(value, "[REDACTED]")
    for pattern in _TOKEN_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return " ".join(cleaned.split())


__all__ = ["redact"]
