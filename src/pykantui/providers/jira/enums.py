"""Closed Jira request vocabularies shared by API callers."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import TypeVar


class JiraFieldType(StrEnum):
    """Field categories accepted by Jira's field-search endpoint."""

    CUSTOM = "custom"
    SYSTEM = "system"


class JiraSprintState(StrEnum):
    """Sprint states accepted by Jira Software's board-sprint endpoint."""

    ACTIVE = "active"
    CLOSED = "closed"
    FUTURE = "future"


_JiraRequestEnum = TypeVar("_JiraRequestEnum", bound=StrEnum)


def enum_values(
    enum_type: type[_JiraRequestEnum],
    values: Iterable[str],
    *,
    label: str,
) -> tuple[str, ...]:
    """Normalize compatible strings or enum members and name invalid values."""

    normalized: list[str] = []
    invalid: list[str] = []
    for value in values:
        try:
            normalized.append(enum_type(value).value)
        except ValueError:
            invalid.append(str(value))
    if invalid:
        raise ValueError(f"unknown Jira {label}: {', '.join(sorted(invalid))}")
    return tuple(normalized)


__all__ = ["JiraFieldType", "JiraSprintState"]
