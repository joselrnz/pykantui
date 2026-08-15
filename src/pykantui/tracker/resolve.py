"""Resolve human editor values to provider IDs without guessing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pykantui.tracker.errors import ProviderError


def comma_values(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def resolve_ids(
    records: Iterable[Mapping[str, Any]],
    requested: str | Sequence[str],
    *,
    id_key: str = "id",
    name_keys: tuple[str, ...] = ("name", "email", "username", "displayName"),
    field_label: str = "value",
) -> list[str]:
    """Return exact IDs for names/emails/IDs, rejecting missing or ambiguous input."""
    items = list(records)
    values = comma_values(requested) if isinstance(requested, str) else tuple(requested)
    resolved: list[str] = []
    for value in values:
        needle = value.strip().casefold()
        matches = [
            str(item.get(id_key, "")).strip()
            for item in items
            if str(item.get(id_key, "")).strip()
            and needle
            in {
                str(item.get(id_key, "")).strip().casefold(),
                *(str(item.get(key, "")).strip().casefold() for key in name_keys),
            }
        ]
        unique = list(dict.fromkeys(matches))
        if len(unique) != 1:
            state = "no" if not unique else "multiple"
            raise ProviderError(
                f"found {state} {field_label} matching {value!r}",
                hint=f"Use an exact {field_label} name, email, username, or ID.",
            )
        resolved.append(unique[0])
    return resolved
