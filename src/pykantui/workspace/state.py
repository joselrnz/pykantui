"""What the tracker said last time.

``.pykantui/state.json`` holds one snapshot per issue, as of the last sync. It
is the third thing in the room, and without it two-way sync cannot work:

* the **file** is what you have now,
* the **tracker** is what they have now,
* the **snapshot** is what both agreed on last time.

Diffing file against snapshot gives *your* changes. Diffing tracker against
snapshot gives *theirs*. Diffing file against tracker gives the two mixed
together and no way to tell them apart -- which is how a sync ends up pushing a
change the tracker itself just made, or reverting one.

It is a cache, not a source of truth. Deleting it costs you the ability to
detect local edits until the next sync rebuilds it; it never costs data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pykantui.config.paths import write_text_atomic
from pykantui.tracker.models import RemoteIssue
from pykantui.workspace.models import SyncPlan

#: Bumped when the snapshot format changes in a way that cannot be read. A
#: mismatch drops the snapshot rather than misreading it -- one sync without
#: local-edit detection beats a sync that reads stale fields as changes.
SCHEMA = 1


class SyncState:
    """The last-synced snapshot of every issue, keyed by issue id."""

    def __init__(
        self,
        issues: dict[str, RemoteIssue] | None = None,
        conflicts: set[str] | None = None,
    ) -> None:
        self.issues: dict[str, RemoteIssue] = dict(issues or {})

        #: Issue ids the last sync found changed on both sides. Recorded here
        #: because detecting one costs a request per card, which is far too
        #: much to spend on opening a board -- and without it the board could
        #: never show a conflict at all.
        self.conflicts: set[str] = set(conflicts or ())

    # ---- reading and writing --------------------------------------------

    @classmethod
    def load(cls, path: Path) -> SyncState:
        """Read the snapshot, or start empty.

        Every failure here is non-fatal by design: a missing, truncated or
        outdated file means "we do not know what changed", which is recoverable.
        Raising would make a corrupted cache block a sync that would have fixed
        it.
        """
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            return cls()

        issues: dict[str, RemoteIssue] = {}
        for raw in document.get("issues", []) or []:
            try:
                issue = RemoteIssue.model_validate(raw)
            except ValueError:
                continue  # one unreadable entry must not lose the other 200
            issues[issue.issue_id] = issue
        conflicts = {str(item) for item in document.get("conflicts", []) or []}
        return cls(issues, conflicts)

    def save(self, path: Path) -> None:
        document = {
            "schema": SCHEMA,
            "issues": [issue.model_dump(mode="json") for issue in self.ordered()],
            "conflicts": sorted(self.conflicts),
        }
        write_text_atomic(path, json.dumps(document, indent=2, ensure_ascii=False))

    # ---- queries ---------------------------------------------------------

    def ordered(self) -> list[RemoteIssue]:
        """Snapshots in a stable order, so the file does not churn in git."""
        return sorted(self.issues.values(), key=lambda issue: (issue.key or "", issue.issue_id))

    def get(self, issue_id: str) -> RemoteIssue | None:
        return self.issues.get(issue_id)

    def remember(self, issue: RemoteIssue) -> None:
        self.issues[issue.issue_id] = issue

    def forget(self, issue_id: str) -> None:
        self.issues.pop(issue_id, None)

    def replace_all(self, issues: list[RemoteIssue]) -> None:
        self.issues = {issue.issue_id: issue for issue in issues}

    def mark_conflicts(self, issue_ids: set[str]) -> None:
        """Record what the sync just found. Replaces, never accumulates --
        a conflict that has been resolved must stop being reported."""
        self.conflicts = set(issue_ids)

    def changed_remotely(self, current: RemoteIssue) -> bool:
        """Whether the tracker has moved this issue since the last sync."""
        previous = self.issues.get(current.issue_id)
        return previous is not None and previous != current

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - debugging aid
        return {"schema": SCHEMA, "count": len(self.issues)}


def update_after_sync(
    state: SyncState,
    issues: list[RemoteIssue],
    created: list[RemoteIssue],
    held_ids: set[str],
    plan: SyncPlan,
    push_edits: bool,
    known_conflicts: set[str] | None,
) -> None:
    """Replace snapshots while retaining held local work and conflicts."""
    detected = {item.previous.issue_id for item in plan.conflicts()} & held_ids
    if not push_edits:
        detected.update(state.conflicts & held_ids)
        detected.update((known_conflicts or set()) & held_ids)
    state.mark_conflicts(detected)

    held_snapshots = [snapshot for issue_id in held_ids if (snapshot := state.get(issue_id))]
    state.replace_all(issues)
    for snapshot in held_snapshots:
        state.remember(snapshot)

    pulled = {issue.issue_id for issue in issues}
    for issue in created:
        if issue.issue_id not in pulled:
            state.remember(issue)
