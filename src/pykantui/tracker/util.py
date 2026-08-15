"""Date parsing that survives three providers' worth of disagreement.

Every one of them stamps timestamps differently -- Jira sends
``2026-08-07T21:03:11.123+0000`` with no colon in the offset, Plane sends
``2026-07-20T10:39:50.910392Z``, Trello sends ``2026-08-07T21:03:11.000Z``.
``datetime.fromisoformat`` on 3.11 handles ``Z`` and colon-less offsets, but
not every provider stays inside ISO 8601, and none of them are worth an
exception when the field was optional anyway.

The rule throughout: a value we cannot read becomes ``None``, never a crash and
never a wrong date. A missing due date is an ordinary thing; a sync that dies
on one is not.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

#: Jira's ``+0000``. Python wants ``+00:00`` before 3.11 and tolerates both
#: after, but normalising keeps the parse identical across versions.
_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")

_FALLBACK_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def parse_datetime(value: Any) -> datetime | None:
    """Read a timestamp from any of the providers, or return ``None``."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    normalised = text.replace("Z", "+00:00") if text.endswith("Z") else text
    normalised = _OFFSET.sub(r"\1:\2", normalised)

    try:
        return datetime.fromisoformat(normalised)
    except ValueError:
        pass
    for fmt in _FALLBACK_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_date(value: Any) -> date | None:
    """Read a plain date -- a due date -- or return ``None``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


def as_naive(value: datetime | None) -> datetime | None:
    """Drop the timezone, keeping the wall-clock time.

    :class:`pykantui.models.Task` uses naive datetimes throughout, and mixing
    the two raises on comparison. Converting at the provider boundary means the
    board never has to think about it.
    """
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def float_or_none(value: Any) -> float | None:
    """A float, or ``None`` where the provider did not give one.

    Used for the optional ordering fields, where "no position" is meaningful
    and must not be flattened into 0.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sort_key(value: Any) -> float:
    """A float that is always comparable, for use as a ``sorted`` key.

    Separate from :func:`float_or_none` because a sort key may not return
    ``None`` -- comparing ``None`` to a float raises, and an optional position
    field is exactly the case where that happens on someone else's data rather
    than in a test.
    """
    parsed = float_or_none(value)
    return 0.0 if parsed is None else parsed


def first_line(text: str, limit: int = 200) -> str:
    """The first meaningful line, for a card title built out of a body."""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:limit]
    return ""


def person_ids(value: Any, key: str) -> tuple[str, ...]:
    """Provider ids out of a person field, whether it holds one or many.

    One helper for both shapes because Jira sends an object where GitHub sends
    a list, and a caller asking "who is on this card" should not have to care
    which it got. Values that are already bare ids (Shortcut's ``owner_ids``)
    are handled by :func:`bare_ids`.
    """
    if isinstance(value, dict):
        found = str(value.get(key, "") or "")
        return (found,) if found else ()
    if isinstance(value, list):
        return tuple(str(item.get(key, "") or "") for item in value if isinstance(item, dict) and item.get(key))
    return ()


def bare_ids(value: Any) -> tuple[str, ...]:
    """A list that is already ids rather than objects."""
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)
