"""Typed navigation results shared by setup modal screens."""

from __future__ import annotations

from enum import StrEnum


class NavigationAction(StrEnum):
    """A modal result that navigates without selecting or cancelling."""

    BACK = "back"
