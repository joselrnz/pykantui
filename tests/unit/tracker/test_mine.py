"""Only my issues reach the markdown, while the cache still sees everything.

Every test here is really about one risk: getting "mine" wrong is silent. A
board that is empty, or full of a colleague's work, looks exactly like a board
that synced correctly. So the cases that matter most are the ones where a
plausible implementation would be wrong and say nothing:

* a namesake -- matching on a display name puts their work on your desk;
* a rename -- matching on a display name empties your board overnight;
* an unresolvable identity -- treating "I don't know who you are" as
  "everything" hands you the whole team;
* a reassignment -- the card leaves your board, and the notes you wrote about
  it must not leave with it.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path

from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.mine import Scope, identify, owns
from pykantui.tracker.models import (
    IssueEdit,
    RemoteColumn,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.spec import Capabilities, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace.sync import ARCHIVE_DIR, sync

ME = "712020:0a1b2c3d"
THEM = "712020:ffffffff"

TODO = RemoteColumn(column_id="c-todo", name="To Do", position=0, group="todo")
COLUMNS = [TODO]
PROJECT = RemoteProject(project_id="P1", key="JPT", name="jira-project-test")


def issue(key: str, **kw: object) -> RemoteIssue:
    base: dict[str, object] = {
        "issue_id": f"id-{key}",
        "key": key,
        "title": f"Title {key}",
        "column_id": TODO.column_id,
        "status": TODO.name,
    }
    base.update(kw)
    return RemoteIssue(**base)  # type: ignore[arg-type]


class IdentityTests(unittest.TestCase):
    def test_a_display_name_is_never_a_handle(self) -> None:
        """The one field that is neither unique nor stable."""
        me = identify(RemoteUser(account_id=ME, display_name="alex", email="j@x.com"))

        self.assertIn(ME.casefold(), me.handles())
        self.assertNotIn("alex", me.handles())

    def test_a_configured_me_is_added_not_substituted(self) -> None:
        """Plane needs it; the others should keep their own id as well."""
        me = identify(RemoteUser(account_id=ME), configured="j@x.com")

        self.assertIn(ME.casefold(), me.handles())
        self.assertIn("j@x.com", me.handles())

    def test_a_configured_me_alone_is_enough(self) -> None:
        """A Plane API key identifies no person, so this is the only source."""
        me = identify(None, configured="j@x.com")

        self.assertEqual({"j@x.com"}, me.handles())

    def test_an_unresolvable_identity_refuses(self) -> None:
        """Never "everything" -- that hands you the whole team's board."""
        with self.assertRaises(ProviderError) as caught:
            identify(RemoteUser())

        self.assertIn("me", str(caught.exception.hint))

    def test_a_user_with_only_a_display_name_is_unresolvable(self) -> None:
        with self.assertRaises(ProviderError):
            identify(RemoteUser(display_name="alex"))

    def test_handles_ignore_case_and_padding(self) -> None:
        me = identify(RemoteUser(email="  Alex@X.com  "))

        self.assertEqual({"alex@x.com"}, me.handles())


class OwnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.me = identify(RemoteUser(account_id=ME, display_name="alex", email="j@x.com"))

    def test_assigned_to_me(self) -> None:
        self.assertTrue(owns(issue("JPT-1", assignee_ids=(ME,)), self.me))

    def test_assigned_to_somebody_else(self) -> None:
        self.assertFalse(owns(issue("JPT-2", assignee_ids=(THEM,)), self.me))

    def test_one_of_several_assignees_is_enough(self) -> None:
        """GitHub, ClickUp, Plane and Shortcut all allow more than one."""
        self.assertTrue(owns(issue("JPT-3", assignee_ids=(THEM, ME)), self.me))

    def test_reporting_something_is_not_doing_it(self) -> None:
        """The default is your workload, not everything you have ever raised."""
        self.assertFalse(owns(issue("JPT-4", reporter_id=ME), self.me))

    def test_reported_can_be_turned_on(self) -> None:
        """Right for a board you run yourself, where raising and owning coincide."""
        card = issue("JPT-5", reporter_id=ME)

        self.assertTrue(owns(card, self.me, Scope(reported=True)))

    def test_a_namesake_is_not_me(self) -> None:
        """The case that makes display-name matching indefensible."""
        theirs = issue("JPT-6", assignee="alex", assignee_ids=(THEM,))

        self.assertFalse(owns(theirs, self.me))

    def test_a_rename_does_not_lose_my_card(self) -> None:
        """Matching on the id survives the tracker renaming me."""
        mine = issue("JPT-7", assignee="Alex Kim (on leave)", assignee_ids=(ME,))

        self.assertTrue(owns(mine, self.me))

    def test_an_email_in_the_display_field_still_matches(self) -> None:
        """For trackers that expose no ids at all, only a name that is an email."""
        mine = issue("JPT-8", assignee="j@x.com")

        self.assertTrue(owns(mine, self.me))

    def test_everything_scope_takes_all(self) -> None:
        self.assertTrue(owns(issue("JPT-9", assignee_ids=(THEM,)), self.me, Scope(everything=True)))

    def test_an_unassigned_card_i_reported_is_not_my_work_by_default(self) -> None:
        """Nobody has picked it up, so it is not on anyone's desk -- including yours."""
        self.assertFalse(owns(issue("JPT-10", reporter_id=ME), self.me))
        self.assertTrue(owns(issue("JPT-10", reporter_id=ME), self.me, Scope(reported=True)))

    def test_an_unassigned_card_somebody_else_reported_is_not(self) -> None:
        self.assertFalse(owns(issue("JPT-11", reporter_id=THEM), self.me))


class MineProvider(Provider):
    spec = ProviderSpec(
        name="mine-rec",
        label="Recorder",
        auth_fields=(ProviderField(name="token", label="T", kind=FieldKind.SECRET),),
        capabilities=Capabilities(writable_fields=("title",)),
    )

    def __init__(self, issues: list[RemoteIssue]) -> None:
        super().__init__({}, {})
        self._issues = list(issues)

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id=ME, display_name="alex")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return COLUMNS

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(list(self._issues))

    def get_issue(self, project_id: str, probe: RemoteIssue) -> RemoteIssue | None:
        return next((i for i in self._issues if i.issue_id == probe.issue_id), None)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)


class WriteMineTests(unittest.TestCase):
    """The whole project is fetched; only your cards land on disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.me = identify(RemoteUser(account_id=ME))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def board(self) -> list[RemoteIssue]:
        return [
            issue("JPT-1", assignee_ids=(ME,)),
            issue("JPT-2", assignee_ids=(THEM,)),
            issue("JPT-3", reporter_id=ME),
            issue("JPT-4", assignee_ids=(THEM,), reporter_id=THEM),
        ]

    def run_sync(self, provider: Provider, **kw: object) -> object:
        options: dict[str, object] = {
            "commit": False,
            "confirm": lambda plan: True,
            "identity": self.me,
        }
        options.update(kw)
        return sync(self.ws, provider, PROJECT, **options)  # type: ignore[arg-type]

    def written(self) -> list[str]:
        """What is on the board -- the archive is not part of it."""
        return sorted(path.stem for path in self.ws.rglob("JPT-*.md") if ARCHIVE_DIR not in path.parts)

    def test_only_the_cards_assigned_to_me_are_written(self) -> None:
        report = self.run_sync(MineProvider(self.board()))

        self.assertEqual(["JPT-1"], self.written())
        self.assertEqual(4, report.considered)  # type: ignore[attr-defined]

    def test_reported_scope_widens_it(self) -> None:
        self.run_sync(MineProvider(self.board()), scope=Scope(assigned=True, reported=True))

        self.assertEqual(["JPT-1", "JPT-3"], self.written())

    def test_without_an_identity_everything_is_written(self) -> None:
        """An existing shared mirror must not become personal on upgrade."""
        self.run_sync(MineProvider(self.board()), identity=None)

        self.assertEqual(["JPT-1", "JPT-2", "JPT-3", "JPT-4"], self.written())

    def test_the_everything_scope_writes_everything(self) -> None:
        self.run_sync(MineProvider(self.board()), scope=Scope(everything=True))

        self.assertEqual(["JPT-1", "JPT-2", "JPT-3", "JPT-4"], self.written())

    def test_a_card_reassigned_away_is_archived_not_deleted(self) -> None:
        """The notes you wrote are yours; a reassignment must not destroy them."""
        provider = MineProvider(self.board())
        self.run_sync(provider)
        mine = next(self.ws.rglob("JPT-1.md"))
        mine.write_text(mine.read_text(encoding="utf-8") + "\nMy private notes.\n", encoding="utf-8")

        # Somebody takes it off me.
        provider._issues[0] = issue("JPT-1", assignee_ids=(THEM,))
        report = self.run_sync(provider, push_edits=False)

        self.assertEqual(["JPT-1.md"], report.archived)  # type: ignore[attr-defined]
        self.assertEqual([], report.deleted)  # type: ignore[attr-defined]
        archived = self.ws / ARCHIVE_DIR / "JPT-1.md"
        self.assertTrue(archived.exists(), "the file was not archived")
        self.assertIn("My private notes.", archived.read_text(encoding="utf-8"))
        self.assertNotIn("JPT-1", self.written())

    def test_a_card_deleted_on_the_tracker_is_still_deleted(self) -> None:
        """Archiving must not make real deletion impossible."""
        provider = MineProvider(self.board())
        self.run_sync(provider)

        provider._issues = [i for i in provider._issues if i.key != "JPT-1"]
        report = self.run_sync(provider, push_edits=False)

        self.assertEqual(["JPT-1.md"], report.deleted)  # type: ignore[attr-defined]
        self.assertEqual([], report.archived)  # type: ignore[attr-defined]

    def test_a_card_assigned_to_me_appears(self) -> None:
        provider = MineProvider(self.board())
        self.run_sync(provider)
        self.assertNotIn("JPT-2", self.written())

        provider._issues[1] = issue("JPT-2", assignee_ids=(ME,))
        self.run_sync(provider, push_edits=False)

        self.assertIn("JPT-2", self.written())

    def test_the_board_file_lists_only_mine(self) -> None:
        self.run_sync(MineProvider(self.board()))

        board = next(self.ws.rglob("*.md"), None)
        text = "\n".join(p.read_text(encoding="utf-8") for p in self.ws.rglob("BOARD.md"))
        if text:
            self.assertNotIn("JPT-2", text)
        self.assertIsNotNone(board)

    def test_the_snapshot_holds_only_mine(self) -> None:
        """Otherwise the next sync sees every colleague's card as locally deleted."""
        from pykantui.workspace import layout
        from pykantui.workspace.state import SyncState

        self.run_sync(MineProvider(self.board()))

        state = SyncState.load(layout.state_file(self.ws))
        self.assertIsNotNone(state.get("id-JPT-1"))
        self.assertIsNone(state.get("id-JPT-2"))

    def test_an_archived_file_is_not_read_back_as_a_board_card(self) -> None:
        """It sits outside the project tree, so a later sync cannot resurrect it."""
        provider = MineProvider(self.board())
        self.run_sync(provider)
        provider._issues[0] = issue("JPT-1", assignee_ids=(THEM,))
        self.run_sync(provider, push_edits=False)

        report = self.run_sync(provider, push_edits=False)

        self.assertEqual([], report.archived, "the archived file was picked up again")  # type: ignore[attr-defined]
        self.assertEqual([], report.deleted)  # type: ignore[attr-defined]
        self.assertTrue((self.ws / ARCHIVE_DIR / "JPT-1.md").exists())


class ScopeTests(unittest.TestCase):
    def test_the_default_is_assigned_only(self) -> None:
        """Assigned is the work you have to do; reported is paperwork."""
        scope = Scope()

        self.assertTrue(scope.assigned)
        self.assertFalse(scope.reported)
        self.assertFalse(scope.everything)

    def test_an_empty_scope_is_detectable(self) -> None:
        """It would match nothing, which looks like a broken sync."""
        self.assertTrue(Scope(assigned=False, reported=False).is_empty())

    def test_a_scope_is_frozen(self) -> None:
        """So nothing can widen "mine" halfway through a sync."""
        from pydantic import ValidationError

        scope = Scope()
        with self.assertRaises(ValidationError):
            scope.assigned = False


if __name__ == "__main__":
    unittest.main()
