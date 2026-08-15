"""Syncing when the workspace or the tracker is in an awkward state.

These are the situations that produce data loss rather than an error message:
a file that cannot be identified, a snapshot that cannot be read, a column that
was renamed while you were not looking. In every one of them the safe answer is
the same -- do not destroy anything you cannot re-derive -- and that is what
these assert.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import IssueEdit, RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace import layout
from pykantui.workspace.locking import exclusive_workspace
from pykantui.workspace.models import ConflictResolution, PendingPush, SyncPlan, SyncReport
from pykantui.workspace.outbound import apply_plan
from pykantui.workspace.state import SyncState
from pykantui.workspace.sync import sync

TODO = RemoteColumn(column_id="c1", name="To Do", position=0, group="todo")
DOING = RemoteColumn(column_id="c2", name="In Progress", position=1, group="started")
PROJECT = RemoteProject(project_id="P1", key="ACME", name="widgets")


def issue(key: str, column: RemoteColumn = TODO, **kw: object) -> RemoteIssue:
    base: dict[str, object] = {
        "issue_id": f"id-{key}",
        "key": key,
        "title": f"Title {key}",
        "column_id": column.column_id,
        "status": column.name,
    }
    base.update(kw)
    return RemoteIssue(**base)  # type: ignore[arg-type]


class Fake(Provider):
    spec = ProviderSpec(
        name="edge",
        label="Edge",
        auth_fields=(ProviderField(name="token", label="T", kind=FieldKind.SECRET),),
        capabilities=Capabilities(move_issues=True, writable_fields=("title", "body", "column_id")),
    )

    def __init__(self, issues: list[RemoteIssue], columns: list[RemoteColumn] | None = None) -> None:
        super().__init__({}, {})
        self._issues = list(issues)
        self._columns = list(columns or [TODO, DOING])
        self.updates: list[tuple[str, IssueEdit]] = []
        self.refreshes = 0

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me", display_name="alex")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return list(self._columns)

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(list(self._issues))

    def get_issue(self, project_id: str, probe: RemoteIssue) -> RemoteIssue | None:
        return next((i for i in self._issues if i.issue_id == probe.issue_id), None)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)
        self.updates.append((issue.display_key(), edit))

    def refresh(self) -> None:
        self.refreshes += 1
        super().refresh()


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def sync(self, provider: Provider, **kw: object) -> object:
        options: dict[str, object] = {"commit": False, "confirm": lambda plan: True}
        options.update(kw)
        return sync(self.ws, provider, PROJECT, **options)  # type: ignore[arg-type]

    def files(self) -> list[str]:
        return sorted(p.name for p in self.ws.rglob("ACME-*.md"))

    def find(self, key: str) -> Path:
        found = list(self.ws.rglob(f"{key}.md"))
        self.assertEqual(1, len(found), f"expected one {key}.md, got {found}")
        return found[0]

    @staticmethod
    def edit_title(path: Path, before: str, after: str) -> None:
        path.write_text(
            path.read_text(encoding="utf-8").replace(f"title: {before}", f"title: {after}"),
            encoding="utf-8",
        )


def apply_edit(issue_value: RemoteIssue, edit: IssueEdit) -> RemoteIssue:
    """Apply one neutral edit to the fake provider's immutable issue."""
    updates: dict[str, object] = {}
    for field_name in edit.touched():
        value = getattr(edit, field_name)
        if field_name in edit.cleared:
            value = () if field_name == "labels" else None if field_name == "due_date" else ""
        updates[field_name] = value
    return issue_value.model_copy(update=updates)


class StatefulFake(Fake):
    """Provider fake whose accepted writes are visible on the next pull."""

    fail_key = ""
    interrupt_key = ""

    def update_issue(self, issue_value: RemoteIssue, edit: IssueEdit) -> None:
        if issue_value.key == self.interrupt_key:
            raise KeyboardInterrupt
        if issue_value.key == self.fail_key:
            raise ProviderError("provider rejected this card")
        super().update_issue(issue_value, edit)
        self._issues = [
            apply_edit(current, edit) if current.issue_id == issue_value.issue_id else current
            for current in self._issues
        ]


class ConfirmationIdentityTests(unittest.TestCase):
    def test_remote_snapshot_changes_invalidate_an_existing_confirmation(self) -> None:
        before = issue("ACME-1")
        local = IssueEdit(title="Local title")
        first_remote = before.model_copy(update={"priority": "High"})
        later_remote = before.model_copy(update={"priority": "Critical"})
        first = SyncPlan(
            pushes=[PendingPush(key="ACME-1", previous=before, edit=local, remote=first_remote, conflict=True)]
        )
        later = SyncPlan(
            pushes=[PendingPush(key="ACME-1", previous=before, edit=local, remote=later_remote, conflict=True)]
        )

        self.assertNotEqual(first.outbound_token(), later.outbound_token())


class PerFieldConflictResolutionTests(unittest.TestCase):
    def test_provider_and_local_choices_can_resolve_different_fields_on_one_card(self) -> None:
        before = issue("ACME-1", title="Original", body="Original body")
        remote = before.model_copy(update={"title": "Provider title", "body": "Provider body"})
        pending = PendingPush(
            key="ACME-1",
            previous=before,
            remote=remote,
            edit=IssueEdit(title="Local title", body="Local body"),
            conflict=True,
        )
        provider = StatefulFake([remote])
        report = SyncReport()

        sent = apply_plan(
            provider,
            SyncPlan(pushes=[pending]),
            report,
            conflict_resolutions={
                before.issue_id: {
                    "title": ConflictResolution.PROVIDER,
                    "body": ConflictResolution.LOCAL,
                }
            },
        )

        self.assertEqual({before.issue_id}, sent)
        self.assertEqual("Provider title", provider._issues[0].title)
        self.assertEqual("Local body", provider._issues[0].body)
        self.assertEqual(("body",), provider.updates[0][1].touched())
        self.assertEqual(["ACME-1"], report.accepted)

    def test_an_undecided_conflicting_field_holds_the_entire_card(self) -> None:
        before = issue("ACME-1", title="Original")
        remote = before.model_copy(update={"title": "Provider title"})
        pending = PendingPush(
            key="ACME-1",
            previous=before,
            remote=remote,
            edit=IssueEdit(title="Local title"),
            conflict=True,
        )
        provider = StatefulFake([remote])
        report = SyncReport()

        sent = apply_plan(
            provider,
            SyncPlan(pushes=[pending]),
            report,
            conflict_resolutions={before.issue_id: {"title": ConflictResolution.HOLD}},
        )

        self.assertEqual(set(), sent)
        self.assertEqual([], provider.updates)
        self.assertIn("not decided", report.skipped[0][1])

    def test_use_provider_keeps_nonconflicting_local_edits_and_accounts_once(self) -> None:
        before = issue("ACME-1", title="Original", body="Original body")
        remote = before.model_copy(update={"title": "Provider title"})
        pending = PendingPush(
            key="ACME-1",
            previous=before,
            remote=remote,
            edit=IssueEdit(title="Local title", body="Local body"),
            conflict=True,
        )
        provider = StatefulFake([remote])
        report = SyncReport()

        sent = apply_plan(
            provider,
            SyncPlan(pushes=[pending]),
            report,
            accept_remote_conflicts=True,
            conflict_resolutions={
                before.issue_id: {"title": ConflictResolution.PROVIDER}
            },
        )

        self.assertEqual({before.issue_id}, sent)
        self.assertEqual("Provider title", provider._issues[0].title)
        self.assertEqual("Local body", provider._issues[0].body)
        self.assertEqual(1, len(provider.updates))
        self.assertEqual(("body",), provider.updates[0][1].touched())
        self.assertEqual(["ACME-1"], report.accepted)
        self.assertEqual(["ACME-1"], report.pushed)

    def test_global_accept_remote_conflicts_also_sends_ready_local_fields(self) -> None:
        before = issue("ACME-1", title="Original", body="Original body")
        remote = before.model_copy(update={"title": "Provider title"})
        pending = PendingPush(
            key="ACME-1",
            previous=before,
            remote=remote,
            edit=IssueEdit(title="Local title", body="Local body"),
            conflict=True,
        )
        provider = StatefulFake([remote])
        report = SyncReport()

        sent = apply_plan(
            provider,
            SyncPlan(pushes=[pending]),
            report,
            accept_remote_conflicts=True,
        )

        self.assertEqual({before.issue_id}, sent)
        self.assertEqual("Provider title", provider._issues[0].title)
        self.assertEqual("Local body", provider._issues[0].body)
        self.assertEqual(("body",), provider.updates[0][1].touched())
        self.assertEqual(["ACME-1"], report.accepted)
        self.assertEqual(["ACME-1"], report.pushed)


class ProviderWriteRecoveryTests(Case):
    def _seed_three_local_edits(self, provider: StatefulFake) -> None:
        self.sync(provider)
        for number in range(1, 4):
            self.edit_title(
                self.find(f"ACME-{number}"),
                f"Title ACME-{number}",
                f"Local ACME-{number}",
            )

    def test_a_failed_middle_write_does_not_block_safe_later_cards(self) -> None:
        provider = StatefulFake([issue(f"ACME-{number}") for number in range(1, 4)])
        self._seed_three_local_edits(provider)
        provider.fail_key = "ACME-2"

        report = self.sync(provider)

        self.assertEqual(["ACME-1", "ACME-3"], report.pushed)  # type: ignore[attr-defined]
        self.assertIn(("ACME-2", "provider rejected this card"), report.skipped)  # type: ignore[attr-defined]
        self.assertIn("ACME-2.md", report.held)  # type: ignore[attr-defined]
        self.assertIn("title: Local ACME-2", self.find("ACME-2").read_text(encoding="utf-8"))

    def test_an_interrupted_batch_is_idempotently_recoverable(self) -> None:
        provider = StatefulFake([issue(f"ACME-{number}") for number in range(1, 4)])
        self._seed_three_local_edits(provider)
        provider.interrupt_key = "ACME-2"

        with self.assertRaises(KeyboardInterrupt):
            self.sync(provider)

        self.assertEqual("Local ACME-1", provider._issues[0].title)
        self.assertEqual("Title ACME-2", provider._issues[1].title)
        provider.interrupt_key = ""
        recovered = self.sync(provider)
        self.assertEqual(
            {"Local ACME-1", "Local ACME-2", "Local ACME-3"},
            {item.title for item in provider._issues},
        )
        self.assertEqual([], recovered.held)  # type: ignore[attr-defined]

    def test_a_disk_failure_after_provider_success_keeps_markdown_for_recovery(self) -> None:
        provider = StatefulFake([issue("ACME-1")])
        self.sync(provider)
        path = self.find("ACME-1")
        self.edit_title(path, "Title ACME-1", "Local title")

        with (
            patch("pykantui.workspace.sync.write_issues", side_effect=OSError("disk full")),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            self.sync(provider)

        self.assertEqual("Local title", provider._issues[0].title)
        self.assertIn("title: Local title", path.read_text(encoding="utf-8"))
        self.assertIsNotNone(self.sync(provider))

    def test_an_after_sync_git_failure_reports_provider_success_and_recovers(self) -> None:
        provider = StatefulFake([issue("ACME-1")])
        self.sync(provider)
        path = self.find("ACME-1")
        self.edit_title(path, "Title ACME-1", "Local title")
        (self.ws / ".git").mkdir()

        with (
            patch("pykantui.workspace.checkpoints.git.available", return_value=True),
            patch("pykantui.workspace.checkpoints.git.ensure_runtime_ignored", return_value=True),
            patch("pykantui.workspace.checkpoints.git.is_repo", return_value=True),
            patch("pykantui.workspace.checkpoints.git.is_dirty", side_effect=[False, True]),
            patch("pykantui.workspace.checkpoints.git.commit", return_value=False),
            self.assertRaisesRegex(ProviderError, "provider sync completed"),
        ):
            self.sync(provider, commit=True)

        self.assertEqual("Local title", provider._issues[0].title)
        self.assertIn("title: Local title", path.read_text(encoding="utf-8"))
        self.assertIsNotNone(self.sync(provider, commit=False))


class BrokenWorkspaceTests(Case):
    def test_workspace_lock_records_and_cleans_process_metadata(self) -> None:
        owner_path = layout.meta_dir(self.ws) / "sync.lock.owner.json"

        with exclusive_workspace(self.ws):
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            self.assertEqual(os.getpid(), owner["pid"])
            self.assertTrue(owner["host"])
            self.assertTrue(owner["started_at"])

        self.assertFalse(owner_path.exists())

    def test_stale_lock_metadata_is_replaced_after_the_os_lock_is_free(self) -> None:
        owner_path = layout.meta_dir(self.ws) / "sync.lock.owner.json"
        owner_path.parent.mkdir(parents=True, exist_ok=True)
        owner_path.write_text('{"pid": 999999, "host": "stale", "started_at": "old"}', encoding="utf-8")

        with exclusive_workspace(self.ws):
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            self.assertEqual(os.getpid(), owner["pid"])

    def test_a_second_sync_cannot_enter_the_same_workspace(self) -> None:
        provider = Fake([issue("ACME-1")])

        with exclusive_workspace(self.ws), self.assertRaisesRegex(ProviderError, "already syncing"):
            self.sync(provider, push_edits=False)

        self.assertEqual(0, provider.refreshes)

    def test_pull_only_always_requests_a_fresh_issue_list(self) -> None:
        provider = Fake([issue("ACME-1")])

        self.sync(provider, push_edits=False)

        self.assertEqual(1, provider.refreshes)

    def test_pull_only_preserves_a_known_conflict_while_the_local_edit_is_held(self) -> None:
        provider = Fake([issue("ACME-1")])
        self.sync(provider)
        path = self.find("ACME-1")
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title ACME-1", "title: Local title"),
            encoding="utf-8",
        )
        state = SyncState.load(layout.state_file(self.ws))
        state.mark_conflicts({"id-ACME-1"})
        state.save(layout.state_file(self.ws))

        self.sync(provider, push_edits=False)

        refreshed = SyncState.load(layout.state_file(self.ws))
        self.assertEqual({"id-ACME-1"}, refreshed.conflicts)

    def test_a_file_with_no_id_is_left_alone(self) -> None:
        """Hand-written, or from an older version. Not ours to delete."""
        self.sync(Fake([issue("ACME-1")]))
        stray = self.ws / "edge" / "projects" / "ACME" / "to-do" / "notes.md"
        stray.write_text("---\ntitle: mine\n---\n\nhand written\n", encoding="utf-8")

        report = self.sync(Fake([issue("ACME-1")]), push_edits=False)

        self.assertTrue(stray.exists(), "a file we cannot identify was deleted")
        self.assertEqual([], report.deleted)  # type: ignore[attr-defined]

    def test_unreadable_frontmatter_is_held_while_other_cards_sync(self) -> None:
        """A corrupt card is never cleared or overwritten, but does not block peers."""
        initial = Fake([issue("ACME-1", assignee="alex", labels=("safe",)), issue("ACME-2")])
        self.sync(initial)
        broken = self.find("ACME-1")
        broken.write_text("---\nid: id-ACME-1\nlabels: security\ndue: 2026-99-99\n---\nbody\n", encoding="utf-8")
        provider = Fake([issue("ACME-1", assignee="alex", labels=("safe",)), issue("ACME-2", title="Updated")])

        report = self.sync(provider)

        self.assertEqual([], provider.updates)
        self.assertIn("labels: security", broken.read_text(encoding="utf-8"))
        self.assertIn("title: Updated", self.find("ACME-2").read_text(encoding="utf-8"))
        self.assertIn("invalid Markdown", " ".join(reason for _, reason in report.skipped))  # type: ignore[attr-defined]

    def test_a_corrupt_state_file_is_survivable(self) -> None:
        """state.json is a cache of the last sync, not the source of truth."""
        self.sync(Fake([issue("ACME-1")]))
        layout.state_file(self.ws).write_text("{not json", encoding="utf-8")

        report = self.sync(Fake([issue("ACME-1")]), push_edits=False)

        self.assertIsNotNone(report)
        self.assertIn("ACME-1.md", self.files())

    def test_a_missing_state_file_is_survivable(self) -> None:
        self.sync(Fake([issue("ACME-1")]))
        layout.state_file(self.ws).unlink()

        self.sync(Fake([issue("ACME-1")]), push_edits=False)

        self.assertIn("ACME-1.md", self.files())

    def test_a_locally_deleted_file_comes_back(self) -> None:
        """The tracker is the authority; deleting a file is not a delete request."""
        self.sync(Fake([issue("ACME-1")]))
        self.find("ACME-1").unlink()

        self.sync(Fake([issue("ACME-1")]), push_edits=False)

        self.assertIn("ACME-1.md", self.files())


class BoardShapeTests(Case):
    def test_a_renamed_column_moves_the_file(self) -> None:
        """The folder is the column, so a rename has to relocate the card."""
        provider = Fake([issue("ACME-1")])
        self.sync(provider)
        self.assertTrue(list(self.ws.rglob("to-do/ACME-1.md")))

        renamed = RemoteColumn(column_id="c1", name="Backlog", position=0, group="todo")
        moved = Fake([issue("ACME-1", renamed)], columns=[renamed, DOING])
        self.sync(moved, push_edits=False)

        self.assertTrue(list(self.ws.rglob("backlog/ACME-1.md")), self.files())
        self.assertFalse(list(self.ws.rglob("to-do/ACME-1.md")), "the old copy was left behind")

    def test_a_column_that_disappears(self) -> None:
        """Its cards move to wherever the tracker now says they are."""
        provider = Fake([issue("ACME-1", DOING)])
        self.sync(provider)

        shrunk = Fake([issue("ACME-1", TODO)], columns=[TODO])
        self.sync(shrunk, push_edits=False)

        self.assertTrue(list(self.ws.rglob("to-do/ACME-1.md")))

    def test_a_board_with_no_columns(self) -> None:
        """Nothing to place cards in; must not raise."""
        report = self.sync(Fake([], columns=[]), push_edits=False)

        self.assertIsNotNone(report)

    def test_an_empty_board(self) -> None:
        report = self.sync(Fake([]), push_edits=False)

        self.assertEqual([], self.files())
        self.assertIsNotNone(report)

    def test_two_columns_that_share_a_folder_name(self) -> None:
        """Ambiguous provider columns fail before either card is written."""
        clash = RemoteColumn(column_id="c9", name="TO-DO", position=2, group="todo")
        provider = Fake([issue("ACME-1"), issue("ACME-2", clash)], columns=[TODO, clash])

        with self.assertRaisesRegex(ProviderError, "To Do.*TO-DO.*to-do"):
            self.sync(provider, push_edits=False)

        self.assertEqual([], self.files())


class IdentityTests(Case):
    def test_a_key_that_changes_keeps_one_file(self) -> None:
        """A Jira issue moved between projects gets a new key, same id."""
        self.sync(Fake([issue("ACME-1")]))

        renamed = RemoteIssue(
            issue_id="id-ACME-1", key="OTHER-9", title="Title ACME-1", column_id=TODO.column_id, status=TODO.name
        )
        self.sync(Fake([renamed]), push_edits=False)

        remaining = sorted(p.name for p in self.ws.rglob("*.md") if p.name != "PROJECT.md")
        self.assertEqual(["OTHER-9.md"], remaining, "the old filename survived the rename")

    def test_an_issue_with_no_key_is_still_written(self) -> None:
        keyless = RemoteIssue(issue_id="id-99", key="", title="No key", column_id=TODO.column_id, status=TODO.name)

        self.sync(Fake([keyless]), push_edits=False)

        self.assertTrue(list(self.ws.rglob("*99*.md")), sorted(p.name for p in self.ws.rglob("*.md")))

    def test_titles_that_differ_only_by_case(self) -> None:
        """Windows filesystems are case-insensitive; keys must not collide."""
        provider = Fake([issue("ACME-1"), issue("acme-2")])

        self.sync(provider, push_edits=False)

        self.assertEqual(2, len(list(self.ws.rglob("*.md"))) - 1)


class UnicodeTests(Case):
    def test_a_unicode_title_and_body_survive_a_sync(self) -> None:
        card = issue("ACME-1", title="進行中 ✅ café", body="日本語のテキスト")

        self.sync(Fake([card]), push_edits=False)

        text = self.find("ACME-1").read_text(encoding="utf-8")
        self.assertIn("進行中", text)
        self.assertIn("日本語", text)

    def test_a_unicode_column_name(self) -> None:
        column = RemoteColumn(column_id="c1", name="進行中", position=0, group="started")
        provider = Fake([issue("ACME-1", column)], columns=[column])

        self.sync(provider, push_edits=False)

        self.assertEqual(["ACME-1.md"], self.files())


class LargePlanTests(unittest.TestCase):
    def test_five_hundred_mixed_conflicts_have_stable_bounded_previews(self) -> None:
        pushes: list[PendingPush] = []
        for index in range(500):
            previous = issue(f"ACME-{index}", body="before")
            remote = previous.model_copy(
                update={"title": f"provider {index} " + "x" * 300}
            )
            pushes.append(
                PendingPush(
                    key=previous.display_key(),
                    previous=previous,
                    remote=remote,
                    edit=IssueEdit(title=f"local {index} " + "y" * 300, body=f"safe body {index}"),
                    conflict=True,
                )
            )
        plan = SyncPlan(pushes=pushes)

        preview = plan.describe_blocked()
        token = plan.outbound_token()

        self.assertTrue(preview.startswith("BLOCKED (500)"))
        self.assertEqual(500, len(token))
        self.assertLess(max(len(line) for line in preview.splitlines()), 100)
        self.assertEqual(("title",), plan.pushes[-1].conflicting_fields())


class RepeatedSyncTests(Case):
    def test_a_second_sync_changes_nothing(self) -> None:
        """Otherwise every run produces a git diff made of nothing."""
        provider = Fake([issue("ACME-1", body="text", labels=("a", "b"))])
        self.sync(provider)
        before = self.find("ACME-1").read_bytes()

        report = self.sync(provider, push_edits=False)

        self.assertEqual(before, self.find("ACME-1").read_bytes(), "the file churned")
        self.assertEqual([], report.written)  # type: ignore[attr-defined]

    def test_ten_syncs_are_stable(self) -> None:
        provider = Fake([issue("ACME-1"), issue("ACME-2", DOING)])
        self.sync(provider)
        before = {p.name: p.read_bytes() for p in self.ws.rglob("*.md")}

        for _ in range(10):
            self.sync(provider, push_edits=False)

        after = {p.name: p.read_bytes() for p in self.ws.rglob("*.md")}
        self.assertEqual(before, after)

    def test_the_snapshot_matches_what_was_written(self) -> None:
        provider = Fake([issue("ACME-1")])
        self.sync(provider)

        state = SyncState.load(layout.state_file(self.ws))
        self.assertIsNotNone(state.get("id-ACME-1"))


if __name__ == "__main__":
    unittest.main()


class DuplicateIdTests(Case):
    """Two files claiming the same issue.

    Happens when someone copies a card to keep a variant, or restores one from
    a backup. Only one can be the issue -- but the loser used to be dropped in
    silence, so an edit made in it vanished with no message, which looks exactly
    like a sync deciding your change was not worth sending.
    """

    def duplicate(self, provider: Provider) -> Path:
        self.sync(provider)
        original = self.find("ACME-1")
        copy = original.parent / "ACME-1-copy.md"
        copy.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
        return copy

    def test_the_loser_is_reported(self) -> None:
        provider = Fake([issue("ACME-1")])
        self.duplicate(provider)

        report = self.sync(provider, push_edits=False)

        self.assertEqual(1, len(report.skipped), report.skipped)  # type: ignore[attr-defined]
        self.assertIn("same id", report.skipped[0][1])  # type: ignore[attr-defined]

    def test_the_message_names_the_winner(self) -> None:
        """Otherwise "skipped" tells you nothing about which copy to look at."""
        provider = Fake([issue("ACME-1")])
        self.duplicate(provider)

        report = self.sync(provider, push_edits=False)
        loser, why = report.skipped[0]  # type: ignore[attr-defined]

        self.assertIn(".md", why)
        self.assertNotIn(loser, why.split("(")[-1], "the loser was named as the winner")

    def test_an_edit_in_the_duplicate_is_not_silently_dropped(self) -> None:
        """Either it is sent or it is reported -- never neither."""
        provider = Fake([issue("ACME-1")])
        copy = self.duplicate(provider)
        copy.write_text(
            copy.read_text(encoding="utf-8").replace("title: Title ACME-1", "title: Edited"),
            encoding="utf-8",
        )

        report = self.sync(provider)

        pushed = [edit.title for _, edit in provider.updates]
        self.assertTrue(
            "Edited" in pushed or report.skipped,  # type: ignore[attr-defined]
            "the duplicate's edit was neither sent nor reported",
        )

    def test_the_workspace_collapses_back_to_one_file(self) -> None:
        provider = Fake([issue("ACME-1")])
        self.duplicate(provider)

        self.sync(provider, push_edits=False)

        self.assertEqual(["ACME-1.md"], self.files())

    def test_three_copies_report_two_losers(self) -> None:
        provider = Fake([issue("ACME-1")])
        original = self.duplicate(provider).parent / "ACME-1.md"
        (original.parent / "ACME-1-second.md").write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

        report = self.sync(provider, push_edits=False)

        self.assertEqual(2, len(report.skipped), report.skipped)  # type: ignore[attr-defined]
