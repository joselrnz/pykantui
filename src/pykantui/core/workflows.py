"""The default board shape, and the Jira statuses that map onto it.

One place defines both, so a local board and a Jira board have the same four
columns and a card means the same thing in either.

Several statuses share a column on purpose. That is why transitions are matched
on column id rather than status name — see ``sync/jira.py``.
"""

from __future__ import annotations

from pykantui.models import (
    ARCHIVE_COLUMN,
    DONE_COLUMN,
    IN_PROGRESS_COLUMN,
    NEEDS_REVIEW_COLUMN,
    TODO_COLUMN,
    WAITING_COLUMN,
    Column,
)

# The column-id constants are defined in models/task.py (Board's defaults need them
# without an import cycle) and re-exported here, so this module is the one place
# to look for the board's shape.
__all__ = [
    "ARCHIVE_COLUMN",
    "DEFAULT_COLUMNS",
    "DONE_COLUMN",
    "FINISH_COLUMN",
    "IN_PROGRESS_COLUMN",
    "JIRA_STATUS_MAP",
    "NEEDS_REVIEW_COLUMN",
    "RESET_COLUMN",
    "START_COLUMN",
    "TODO_COLUMN",
    "WAITING_COLUMN",
    "normalise_status",
]

#: Landing in one of these is what stamps a task's start and finish dates.
#: Waiting is deliberately none of them: a paused task keeps the start date it
#: already had, and is not finished.
RESET_COLUMN = TODO_COLUMN
START_COLUMN = IN_PROGRESS_COLUMN
FINISH_COLUMN = DONE_COLUMN

DEFAULT_COLUMNS = [
    Column(column_id=TODO_COLUMN, name="To Do", position=0),
    Column(column_id=IN_PROGRESS_COLUMN, name="In Progress", position=1),
    # Needs Review is a stage of the work, not a parked state: it sits in the
    # flow between In Progress and Done.
    Column(column_id=NEEDS_REVIEW_COLUMN, name="Needs Review", position=2),
    # Waiting is the parked state — needs info, on hold. Work can land here from
    # anywhere, which is why it is not on the straight line to Done.
    Column(column_id=WAITING_COLUMN, name="Waiting", position=3),
    Column(column_id=DONE_COLUMN, name="Done", position=4),
    Column(column_id=ARCHIVE_COLUMN, name="Archive", position=5, visible=False),
]

#: Jira status name -> board column. Matched case-insensitively and with
#: surrounding whitespace ignored, so "To Do", "TO DO" and "to do" all land in
#: the same place. Add rows here rather than renaming statuses in Jira.
JIRA_STATUS_MAP: dict[str, int] = {
    "BACKLOG": TODO_COLUMN,
    "TO DO": TODO_COLUMN,
    "IN PROGRESS": IN_PROGRESS_COLUMN,
    "NEEDS REVIEW": NEEDS_REVIEW_COLUMN,
    "NEEDS MORE INFO": WAITING_COLUMN,
    "WAITING ON HOLD": WAITING_COLUMN,
    "WAITING OR ON HOLD": WAITING_COLUMN,
    "DONE": DONE_COLUMN,
    "CANCEL": DONE_COLUMN,
}


def normalise_status(status: str) -> str:
    """Key a status for lookup, ignoring case and surrounding whitespace."""
    return " ".join(status.split()).casefold()
