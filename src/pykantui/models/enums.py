"""The small closed vocabularies the rest of the package agrees on.

Everything here is an enum rather than a bare string or int. That is not
decoration: these values are written to ``config.json``, parsed back out of it,
and compared in a dozen places. As enums, pydantic rejects a typo at the edge
instead of letting it travel inwards and quietly mean "not that one".
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pykantui.i18n import translate as _


class MovementMode(StrEnum):
    """How ``H``/``L`` behave when moving a card between columns."""

    ADJACENT = "adjacent"
    """Commit the move to the neighbouring column immediately."""

    JUMP = "jump"
    """Highlight a target column and wait for ``enter`` to commit."""


class BackendKind(StrEnum):
    """Which store is behind the board."""

    JSON = "json"
    JIRA = "jira"


class BoardLayout(StrEnum):
    """The central workspace presentation; Kanban is always Home."""

    ROWS = "rows"
    KANBAN = "kanban"
    SPLIT = "split"

    @property
    def glyph(self) -> str:
        return {
            BoardLayout.ROWS: "▤",
            BoardLayout.KANBAN: "▥",
            BoardLayout.SPLIT: "▦",
        }[self]

    @property
    def label(self) -> str:
        return f"{self.glyph} {_(self.value.title())}"


class ColumnRole(StrEnum):
    """What a column *means*, beyond its name.

    Roles are held as column ids rather than positions, so reordering columns
    never silently changes which one means "done".
    """

    RESET = "reset"
    """Landing here clears both timestamps: the card genuinely restarts."""

    START = "start"
    """Landing here stamps ``started_at``, if it is not already set."""

    FINISH = "finish"
    """Landing here stamps ``finished_at``."""

    @property
    def field(self) -> str:
        """The ``BoardConfig`` field holding this role's column id."""
        return f"{self.value}_column"


class Edges(StrEnum):
    """Corner style, applied to every border in the app at once."""

    ROUND = "round"
    SQUARE = "square"


class MenuLevel(IntEnum):
    """How much of the top bar is showing.

    An ``IntEnum`` because it is saved to ``config.json`` as a number and cycles
    by addition. Ordering is meaningful: widgets test ``level >= TOOLBAR``.
    """

    COLLAPSED = 0
    """One line: the menu glyph, the card count and the caret."""

    TOOLBAR = 1
    """Adds the search box and the menu labels."""

    EXPANDED = 2
    """Adds the filter panel underneath."""

    @property
    def next(self) -> MenuLevel:
        """The level the caret cycles to, wrapping back to collapsed."""
        return MenuLevel((self + 1) % len(MenuLevel))
