"""Working out what a column *means*, once, for every tracker.

Every provider faces the same problem: it has a column called "In Review" or
"Working on it" and has to say which of :data:`~pykantui.tracker.models.
COLUMN_GROUPS` that is. Some trackers help -- Linear types its states, Jira
categorises its statuses, Plane groups them -- and some say nothing at all, so
the name is the only evidence there is.

This module was extracted after the same table had been copied into the
providers. Copies drift: a name added to one is missing from the others,
and the bug shows up as a card in the wrong column on one tracker only.

The resolution order is the interesting part, and it is the same everywhere:

1. **Specific names win over any type.** "In Review" is categorised
   ``indeterminate`` by Jira and ``started`` by Linear -- both of which lose
   exactly the distinction a board cares about. A name that says "review" or
   "backlog" is better evidence than either.
2. **Then the tracker's own type**, where it gave us one. It is authoritative
   for the coarse question and immune to renaming.
3. **Then the general names**, which is all that is left for Trello, GitHub,
   Asana and Monday, and for Jira's board path -- a board's column
   configuration reports names and status ids but no categories at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_CANCELLED,
    COLUMN_DONE,
    COLUMN_REVIEW,
    COLUMN_STARTED,
    COLUMN_TODO,
    COLUMN_UNKNOWN,
    ColumnGroup,
)

#: Names carrying a meaning no tracker's own type can express. Checked first,
#: so they beat the type rather than being overruled by it.
SPECIFIC_NAMES: tuple[tuple[str, ColumnGroup], ...] = (
    ("backlog", COLUMN_BACKLOG),
    ("icebox", COLUMN_BACKLOG),
    ("someday", COLUMN_BACKLOG),
    ("triage", COLUMN_BACKLOG),
    ("in review", COLUMN_REVIEW),
    ("code review", COLUMN_REVIEW),
    ("review", COLUMN_REVIEW),
    ("qa", COLUMN_REVIEW),
    ("testing", COLUMN_REVIEW),
    ("verify", COLUMN_REVIEW),
)

#: Ordinary column names, checked last. Order matters: the first substring to
#: match wins, so the more specific phrasings come before the looser ones.
GENERAL_NAMES: tuple[tuple[str, ColumnGroup], ...] = (
    ("not started", COLUMN_TODO),
    ("in progress", COLUMN_STARTED),
    ("progress", COLUMN_STARTED),
    ("working on it", COLUMN_STARTED),
    ("doing", COLUMN_STARTED),
    ("started", COLUMN_STARTED),
    ("active", COLUMN_STARTED),
    ("current", COLUMN_STARTED),
    ("stuck", COLUMN_STARTED),
    ("blocked", COLUMN_STARTED),
    ("wontfix", COLUMN_CANCELLED),
    ("won't fix", COLUMN_CANCELLED),
    ("won't do", COLUMN_CANCELLED),
    ("cancel", COLUMN_CANCELLED),
    ("rejected", COLUMN_CANCELLED),
    ("duplicate", COLUMN_CANCELLED),
    ("abandoned", COLUMN_CANCELLED),
    ("done", COLUMN_DONE),
    ("complete", COLUMN_DONE),
    ("closed", COLUMN_DONE),
    ("resolved", COLUMN_DONE),
    ("shipped", COLUMN_DONE),
    ("released", COLUMN_DONE),
    ("merged", COLUMN_DONE),
    ("to do", COLUMN_TODO),
    ("todo", COLUMN_TODO),
    ("open", COLUMN_TODO),
    ("new", COLUMN_TODO),
    ("ready", COLUMN_TODO),
    ("planned", COLUMN_TODO),
    ("upcoming", COLUMN_TODO),
)


def resolve_group(
    name: str,
    *,
    type_key: str = "",
    type_map: Mapping[str, ColumnGroup] | None = None,
    extra_names: Iterable[tuple[str, ColumnGroup]] = (),
) -> ColumnGroup:
    """Classify one column.

    ``type_map`` is the tracker's own vocabulary -- Linear's state types,
    Jira's status categories, Plane's state groups -- and ``type_key`` is the
    value this column carries. Providers with no such concept pass neither and
    get the name-only path.

    ``extra_names`` lets a provider add phrasings peculiar to it without
    forking the shared tables.

    Returns :data:`~pykantui.tracker.models.COLUMN_UNKNOWN` rather than
    guessing when nothing matches. Honest beats confident: the caller can fall
    back on position, and a wrong group silently misfiles a card.
    """
    lowered = name.strip().lower()

    for needle, group in (*extra_names, *SPECIFIC_NAMES):
        if needle in lowered:
            return group

    if type_key and type_map:
        found = type_map.get(type_key.strip().lower())
        if found:
            return found

    for needle, group in GENERAL_NAMES:
        if needle in lowered:
            return group

    return COLUMN_UNKNOWN


def group_from_name(
    name: str,
    extra_names: Iterable[tuple[str, ColumnGroup]] = (),
) -> ColumnGroup:
    """Classify from the name alone, for trackers that type nothing."""
    return resolve_group(name, extra_names=extra_names)
