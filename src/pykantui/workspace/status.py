"""Whether a card agrees with the tracker, and how to say so on a board.

Five states, from the facts already on disk -- the file, the snapshot of what
the tracker last said, and whether the two differ:

``SYNCED``
    The file matches the last sync. Nothing to send.
``EDITED``
    You changed something that has not been sent. **This is the state the board
    could not previously show**, which meant a card with unpushed work looked
    exactly like one without.
``CONFLICT``
    You changed it and so did the tracker. A push would overwrite theirs, so
    the sync skips it unless forced.
``NEW``
    On disk with no snapshot -- never synced. Usually a card created locally
    that has not reached the tracker yet.
``INVALID``
    The Markdown cannot safely be interpreted. It remains visible but no
    provider edit is derived from it.

Deliberately computed from local files only. Working out that a card has
unsent edits must not require a network round trip, or the board stops being
instant and stops working on a train.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pykantui.i18n import ntranslate as ngettext
from pykantui.i18n import translate as _
from pykantui.tracker.models import RemoteColumn, RemoteIssue
from pykantui.workspace import layout, markdown
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.state import SyncState


class SyncStatus(StrEnum):
    SYNCED = "synced"
    EDITED = "edited"
    CONFLICT = "conflict"
    NEW = "new"
    INVALID = "invalid"

    @property
    def marker(self) -> str:
        """The dot drawn on a card.

        A filled circle reads as "needs attention" at a glance and a hollow one
        as "settled", which is the distinction that matters when you are
        scanning a column rather than reading it.
        """
        return {
            SyncStatus.SYNCED: "○",
            SyncStatus.EDITED: "●",
            SyncStatus.CONFLICT: "◍",
            SyncStatus.NEW: "◌",
            SyncStatus.INVALID: "✗",
        }[self]

    @property
    def label(self) -> str:
        return {
            SyncStatus.SYNCED: _("synced"),
            SyncStatus.EDITED: _("unsent edit"),
            SyncStatus.CONFLICT: _("conflict"),
            SyncStatus.NEW: _("not synced"),
            SyncStatus.INVALID: _("invalid Markdown"),
        }[self]

    @property
    def colour(self) -> str:
        """The theme variable this state is drawn in.

        Semantic rather than literal, so the dot follows whatever theme is
        loaded instead of being a hardcoded green that vanishes on a light
        background. Chosen by urgency, not by prettiness:

        * synced is deliberately the *quietest* thing on the card -- it is the
          normal state and should not compete with anything,
        * an unsent edit is a warning: work exists that only you have,
        * a conflict is an error: pushing would overwrite someone,
        * unsynced is merely informational.

        ``$accent`` for unsynced. It used to be ``$primary``, because in stock
        textual-dark ``$accent`` and ``$warning`` are the *same* ``#ffa62b`` --
        so an unsynced card and one with an unsent edit were indistinguishable
        and only the glyph told them apart.

        Synced returns ``""`` -- **no colour at all**. It inherits the muted
        grey the metadata line already uses, which is right twice over: the
        normal state should not compete for attention, and only the states
        that want something get to spend colour. Marking it ``$text-muted``
        was actually worse than nothing -- that variable does not resolve in
        content markup, so it fell back to full-strength white and made the
        quietest state the brightest thing on the card.
        """
        return {
            SyncStatus.SYNCED: "",
            SyncStatus.EDITED: "$warning",
            SyncStatus.CONFLICT: "$error",
            SyncStatus.NEW: "$accent",
            SyncStatus.INVALID: "$error",
        }[self]

    def markup(self) -> str:
        """The dot and its label, as Textual content markup."""
        text = f"{self.marker} {self.label}"
        return f"[{self.colour}]{text}[/]" if self.colour else text

    @property
    def css_class(self) -> str:
        return f"dot-{self.value}"

    def needs_attention(self) -> bool:
        return self is not SyncStatus.SYNCED


def status_of(
    path: Path,
    workspace: Path,
    provider: str,
    project: object,
    state: SyncState,
    columns: list[RemoteColumn],
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
) -> SyncStatus:
    """Classify one issue file.

    Detecting a conflict needs the tracker, and asking costs a request per
    card -- far too much for opening a board. So the last sync **records**
    which issues it found in conflict, and this reads that back. Without it
    ``CONFLICT`` was unreachable: defined, coloured, tested, and impossible to
    ever see.
    """
    try:
        parsed = markdown.read(path)
    except OSError:
        return SyncStatus.NEW
    if not parsed.valid:
        return SyncStatus.INVALID

    issue_id = str(parsed.front.get("id", "") or "")
    previous = state.get(issue_id) if issue_id else None
    if previous is None:
        return SyncStatus.NEW

    folder = layout.column_name_of(path, workspace, provider, project)  # type: ignore[arg-type]
    column = layout.folder_index(columns, column_style).get(folder)
    edit = markdown.edit_from(
        parsed,
        column_id=column.column_id if column else previous.column_id,
        previous=previous,
    )
    if edit.is_empty():
        return SyncStatus.SYNCED
    # Still edited locally, and the last sync said the tracker had moved too.
    return SyncStatus.CONFLICT if issue_id in state.conflicts else SyncStatus.EDITED


def summarise(statuses: list[SyncStatus]) -> str:
    """A one-line count for the board's header.

    Only the states worth acting on are named. "12 cards, all synced" is
    quieter and more useful than a breakdown of zeros.
    """
    counts = {status: statuses.count(status) for status in SyncStatus}
    parts = []
    for status in (SyncStatus.INVALID, SyncStatus.EDITED, SyncStatus.CONFLICT, SyncStatus.NEW):
        count = counts[status]
        if count:
            if status is SyncStatus.INVALID:
                label = _("invalid Markdown")
            elif status is SyncStatus.EDITED:
                label = ngettext("unsent edit", "unsent edits", count)
            elif status is SyncStatus.CONFLICT:
                label = ngettext("conflict", "conflicts", count)
            else:
                label = _("not synced")
            parts.append(f"{count} {label}")
    return ", ".join(parts) if parts else _("all synced")


def status_for_issue(issue: RemoteIssue, state: SyncState) -> SyncStatus:
    """The status of an issue already in memory, without re-reading its file."""
    previous = state.get(issue.issue_id)
    if previous is None:
        return SyncStatus.NEW
    return SyncStatus.SYNCED if previous == issue else SyncStatus.EDITED
