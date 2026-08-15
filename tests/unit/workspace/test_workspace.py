"""The workspace: markdown files, the sync, and the state that ties them.

Driven by a fake provider so the whole layer is testable with no network and no
token. The fake is deliberately dumb -- it returns what it is told to and
records what was pushed at it -- because the behaviour under test is the sync's,
not a tracker's.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path

from pykantui.tracker.base import Provider
from pykantui.tracker.models import IssueEdit, RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace import layout, markdown
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.state import SyncState
from pykantui.workspace.sync import PendingPush, SyncPlan, SyncReport, sync

TODO = RemoteColumn(column_id="1", name="To Do", position=0, group="todo")
DOING = RemoteColumn(column_id="2", name="In Progress", position=1, group="started")
DONE = RemoteColumn(column_id="3", name="Done", position=2, group="done")

PROJECT = RemoteProject(project_id="JPT", key="JPT", name="jira-project-test")


class FakeProvider(Provider):
    spec = ProviderSpec(
        name="fake",
        label="Fake",
        auth_fields=(ProviderField(name="token", label="T", kind=FieldKind.SECRET),),
        capabilities=Capabilities(
            move_issues=True,
            writable_fields=("title", "body", "column_id", "labels", "due_date"),
        ),
    )

    def __init__(
        self,
        issues: list[RemoteIssue],
        columns: list[RemoteColumn] | None = None,
        *,
        can_get_issue: bool = True,
    ) -> None:
        super().__init__({}, {})
        self._issues = issues
        self._columns = columns or [TODO, DOING, DONE]
        self.pushed: list[tuple[str, IssueEdit]] = []
        self.fetched: list[str] = []
        self.refreshed = False
        self.can_get_issue = can_get_issue

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        if not self.can_get_issue:
            return None
        self.fetched.append(issue.issue_id)
        return next((i for i in self._issues if i.issue_id == issue.issue_id), None)

    def refresh(self) -> None:
        self.refreshed = True
        super().refresh()

    def verify(self) -> RemoteUser:
        return RemoteUser(display_name="fake")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return self._columns

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(self._issues)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)
        self.pushed.append((issue.display_key(), edit))


def issue(key: str, column: RemoteColumn, **kw: object) -> RemoteIssue:
    base: dict[str, object] = {
        "issue_id": key.replace("-", ""),
        "key": key,
        "title": f"Title for {key}",
        "column_id": column.column_id,
        "status": column.name,
    }
    base.update(kw)
    return RemoteIssue(**base)  # type: ignore[arg-type]


def record_and_accept(plans: list[SyncPlan]) -> Callable[[SyncPlan], bool]:
    def confirm(plan: SyncPlan) -> bool:
        plans.append(plan)
        return True

    return confirm


class WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_sync(self, provider: FakeProvider, **kw: object) -> SyncReport:
        return sync(self.ws, provider, PROJECT, commit=False, **kw)  # type: ignore[arg-type]

    def file_for(self, key: str) -> Path:
        found = list(self.ws.rglob(f"{key}.md"))
        self.assertEqual(1, len(found), f"expected exactly one {key}.md, found {found}")
        return found[0]


class MarkdownTests(unittest.TestCase):
    def test_round_trip_preserves_the_fields(self) -> None:
        original = issue(
            "JPT-4",
            DOING,
            body="The description.",
            labels=("a", "b"),
            components=("API", "Platform"),
        )
        text = markdown.render(original, column_name="In Progress")
        parsed = markdown.parse(text)
        self.assertEqual("JPT-4", parsed.front["key"])
        self.assertEqual("In Progress", parsed.front["column"])
        self.assertEqual(["a", "b"], parsed.front["labels"])
        self.assertEqual(["API", "Platform"], parsed.front["components"])
        self.assertEqual("The description.", parsed.source)

    def test_notes_are_kept_and_source_is_not(self) -> None:
        """The split that makes editing safe across syncs."""
        text = markdown.render(issue("K-1", TODO, body="from tracker"), column_name="To Do", notes="mine")
        parsed = markdown.parse(text)
        self.assertEqual("from tracker", parsed.source)
        self.assertEqual("mine", parsed.notes)

    def test_empty_fields_are_left_out_of_frontmatter(self) -> None:
        """An empty key is noise, and hides whether a value was ever set."""
        text = markdown.render(issue("K-1", TODO), column_name="To Do")
        self.assertNotIn("priority:", text)
        self.assertNotIn("parent:", text)

    def test_a_file_with_no_frontmatter_still_parses(self) -> None:
        parsed = markdown.parse("just some text\n")
        self.assertEqual({}, parsed.front)
        self.assertEqual("just some text", parsed.source)

    def test_broken_yaml_does_not_lose_the_notes(self) -> None:
        """Losing someone's writing to a stray colon is the worst outcome here."""
        text = "---\nkey: [unclosed\n---\n\nbody\n\n<!-- pykantui:notes -->\nkeep me\n"
        parsed = markdown.parse(text)
        self.assertEqual({}, parsed.front)
        self.assertEqual("keep me", parsed.notes)

    def test_a_file_without_the_notes_marker_has_no_notes(self) -> None:
        """Not: 'the whole body is notes', which would freeze it against syncs."""
        parsed = markdown.parse("---\nkey: K-1\n---\n\nbody text\n")
        self.assertEqual("", parsed.notes)
        self.assertEqual("body text", parsed.source)


class EditDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = issue("JPT-4", DOING, body="original", title="Title for JPT-4")

    def _parsed(self, **overrides: object) -> markdown.IssueFile:
        text = markdown.render(self.previous, column_name="In Progress")
        parsed = markdown.parse(text)
        parsed.front.update(overrides)
        return parsed

    def test_an_untouched_file_produces_no_edit(self) -> None:
        parsed = self._parsed()
        parsed.source = "original"
        edit = markdown.edit_from(parsed, column_id=DOING.column_id, previous=self.previous)
        self.assertTrue(edit.is_empty(), f"unexpected edit: {edit.touched()}")

    def test_a_changed_title_is_detected(self) -> None:
        parsed = self._parsed(title="A new title")
        parsed.source = "original"
        edit = markdown.edit_from(parsed, column_id=DOING.column_id, previous=self.previous)
        self.assertEqual(("title",), edit.touched())

    def test_the_directory_decides_the_column_not_the_frontmatter(self) -> None:
        """Dragging a file between folders is how a card moves."""
        parsed = self._parsed(column="In Progress")  # frontmatter still says the old one
        parsed.source = "original"
        edit = markdown.edit_from(parsed, column_id=DONE.column_id, previous=self.previous)
        self.assertEqual(("column_id",), edit.touched())
        self.assertEqual(DONE.column_id, edit.column_id)

    def test_editing_the_body_is_detected(self) -> None:
        parsed = self._parsed()
        parsed.source = "rewritten by hand"
        edit = markdown.edit_from(parsed, column_id=DOING.column_id, previous=self.previous)
        self.assertEqual(("body",), edit.touched())

    def test_editing_jira_components_in_frontmatter_is_detected(self) -> None:
        self.previous = self.previous.model_copy(update={"components": ("API",)})
        parsed = self._parsed(components=["API", "Platform"])
        parsed.source = "original"

        edit = markdown.edit_from(parsed, column_id=DOING.column_id, previous=self.previous)

        self.assertEqual(("components",), edit.touched())
        self.assertEqual(("API", "Platform"), edit.components)

    def test_editing_provider_issue_type_in_frontmatter_is_detected(self) -> None:
        self.previous = self.previous.model_copy(update={"issue_type": "Task"})
        parsed = self._parsed(type="Bug")
        parsed.source = "original"

        edit = markdown.edit_from(parsed, column_id=DOING.column_id, previous=self.previous)

        self.assertEqual(("issue_type",), edit.touched())
        self.assertEqual("Bug", edit.issue_type)

    def test_removing_optional_issue_type_is_an_explicit_clear(self) -> None:
        self.previous = self.previous.model_copy(update={"issue_type": "Task"})
        parsed = self._parsed()
        parsed.front.pop("type", None)
        parsed.source = "original"

        edit = markdown.edit_from(parsed, column_id=DOING.column_id, previous=self.previous)

        self.assertEqual(("issue_type",), edit.touched())
        self.assertIn("issue_type", edit.cleared)


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip(self) -> None:
        state = SyncState()
        state.remember(issue("K-1", TODO))
        state.save(self.path)
        self.assertEqual("K-1", SyncState.load(self.path).get("K1").key)  # type: ignore[union-attr]

    def test_a_missing_file_is_an_empty_state_not_an_error(self) -> None:
        self.assertEqual({}, SyncState.load(self.path).issues)

    def test_a_corrupt_file_is_discarded_rather_than_raising(self) -> None:
        """A broken cache must not block the sync that would rebuild it."""
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual({}, SyncState.load(self.path).issues)

    def test_an_outdated_schema_is_discarded(self) -> None:
        self.path.write_text('{"schema": 999, "issues": []}', encoding="utf-8")
        self.assertEqual({}, SyncState.load(self.path).issues)


class SyncTests(WorkspaceCase):
    def test_first_sync_writes_the_tree(self) -> None:
        provider = FakeProvider([issue("K-1", TODO), issue("K-2", DOING)])
        report = self.run_sync(provider)
        self.assertEqual(2, len(report.written))
        self.assertTrue((self.ws / "fake/projects/JPT/to-do/K-1.md").is_file())
        self.assertTrue((self.ws / "fake/projects/JPT/in-progress/K-2.md").is_file())

    def test_empty_columns_still_get_a_directory(self) -> None:
        """So the board's shape is visible, and there is somewhere to drag to."""
        self.run_sync(FakeProvider([issue("K-1", TODO)]))
        self.assertTrue((self.ws / "fake/projects/JPT/done").is_dir())

    def test_a_second_sync_changes_nothing(self) -> None:
        """Without this, every run rewrites every file and history is worthless."""
        provider = FakeProvider([issue("K-1", TODO), issue("K-2", DOING)])
        self.run_sync(provider)
        report = self.run_sync(provider)
        self.assertEqual(0, report.total_changes(), report.summary())

    def test_a_card_moving_column_moves_the_file(self) -> None:
        moved = issue("K-1", TODO)
        provider = FakeProvider([moved])
        self.run_sync(provider)
        self.assertTrue((self.ws / "fake/projects/JPT/to-do/K-1.md").is_file())

        provider._issues = [moved.model_copy(update={"column_id": DONE.column_id, "status": "Done"})]
        self.run_sync(provider)
        self.assertFalse((self.ws / "fake/projects/JPT/to-do/K-1.md").exists())
        self.assertTrue((self.ws / "fake/projects/JPT/done/K-1.md").is_file())

    def test_notes_survive_the_card_moving(self) -> None:
        moved = issue("K-1", TODO)
        provider = FakeProvider([moved])
        self.run_sync(provider)
        path = self.file_for("K-1")
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\nmy notes\n", encoding="utf-8")

        provider._issues = [moved.model_copy(update={"column_id": DONE.column_id})]
        self.run_sync(provider)
        self.assertEqual("my notes", markdown.read(self.file_for("K-1")).notes)

    def test_an_issue_that_vanishes_has_its_file_removed(self) -> None:
        provider = FakeProvider([issue("K-1", TODO), issue("K-2", TODO)])
        self.run_sync(provider)
        provider._issues = [issue("K-1", TODO)]
        report = self.run_sync(provider)
        self.assertEqual(["K-2.md"], report.deleted)
        self.assertFalse((self.ws / "fake/projects/JPT/to-do/K-2.md").exists())

    def test_a_local_edit_is_pushed_before_the_pull_overwrites_it(self) -> None:
        """The ordering the whole design rests on."""
        original = issue("K-1", TODO, title="Original")
        provider = FakeProvider([original])
        self.run_sync(provider)

        path = self.file_for("K-1")
        path.write_text(path.read_text(encoding="utf-8").replace("title: Original", "title: Edited"), encoding="utf-8")
        self.run_sync(provider)

        self.assertEqual(1, len(provider.pushed), "the local edit was never sent")
        key, edit = provider.pushed[0]
        self.assertEqual("K-1", key)
        self.assertEqual("Edited", edit.title)

    def test_dragging_a_file_pushes_a_column_change(self) -> None:
        original = issue("K-1", TODO)
        provider = FakeProvider([original])
        self.run_sync(provider)

        source = self.ws / "fake/projects/JPT/to-do/K-1.md"
        target = self.ws / "fake/projects/JPT/done/K-1.md"
        source.replace(target)

        self.run_sync(provider)
        self.assertEqual(1, len(provider.pushed))
        self.assertEqual(DONE.column_id, provider.pushed[0][1].column_id)

    def test_nothing_is_pushed_on_the_very_first_sync(self) -> None:
        """There is no baseline yet, so any 'edit' would be a guess."""
        provider = FakeProvider([issue("K-1", TODO)])
        self.run_sync(provider)
        self.assertEqual([], provider.pushed)

    def test_an_unwritable_field_is_reported_not_raised(self) -> None:
        """One stuck issue must not abandon the other forty."""
        original = issue("K-1", TODO)

        class BodyOnlyProvider(FakeProvider):
            spec = FakeProvider.spec.model_copy(update={"capabilities": Capabilities(writable_fields=("body",))})

        provider = BodyOnlyProvider([original])
        self.run_sync(provider)

        path = self.file_for("K-1")
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title for K-1", "title: Nope"), encoding="utf-8"
        )
        report = self.run_sync(provider)
        self.assertEqual([], provider.pushed)
        self.assertEqual(1, len(report.skipped))
        self.assertIn("title", report.skipped[0][1])

    def test_the_board_file_lists_every_column(self) -> None:
        self.run_sync(FakeProvider([issue("K-1", TODO)]))
        text = (self.ws / "fake/projects/JPT/PROJECT.md").read_text(encoding="utf-8")
        self.assertIn("## To Do (1)", text)
        self.assertIn("## Done (0)", text)
        self.assertIn("_empty_", text)


class SpacesTests(WorkspaceCase):
    """Columns are called "In Progress" in real life. Spaces have to work."""

    def test_no_path_carries_a_space_by_default(self) -> None:
        """Lowercase and dashed, so nothing in the tree ever needs quoting."""
        self.run_sync(FakeProvider([issue("K-1", DOING)]))
        self.assertTrue((self.ws / "fake/projects/JPT/in-progress/K-1.md").is_file())
        for path in self.ws.rglob("*"):
            self.assertNotIn(" ", path.name, f"a space reached the tree at {path}")

    def test_links_need_no_encoding_by_default(self) -> None:
        self.run_sync(FakeProvider([issue("K-1", DOING)]))
        board = (self.ws / "fake/projects/JPT/PROJECT.md").read_text(encoding="utf-8")
        self.assertIn("(in-progress/K-1.md)", board)
        self.assertNotIn("%20", board)

    def test_a_spaced_column_is_encoded_when_that_style_is_chosen(self) -> None:
        """The bug this guards: a space ends a CommonMark link destination, so
        `[K-1](In Progress/K-1.md)` rendered as literal text and every
        reference into a spaced column was dead."""
        self.run_sync(FakeProvider([issue("K-1", DOING)]), column_style=ColumnStyle.NAME)
        board = (self.ws / "fake/projects/JPT/PROJECT.md").read_text(encoding="utf-8")
        self.assertIn("(In%20Progress/K-1.md)", board)
        self.assertNotIn("(In Progress/K-1.md)", board)

    def test_a_moved_file_is_recognised_under_either_style(self) -> None:
        """folder -> column must be built the same way the folders were
        written. Get it wrong and dragging a card silently stops working --
        no error, the move just never reaches the tracker."""
        original = issue("K-1", TODO)
        provider = FakeProvider([original])
        self.run_sync(provider, column_style=ColumnStyle.NAME)

        source = self.ws / "fake/projects/JPT/To Do/K-1.md"
        target = self.ws / "fake/projects/JPT/Done/K-1.md"
        source.replace(target)

        self.run_sync(provider, column_style=ColumnStyle.NAME)
        self.assertEqual(1, len(provider.pushed), "the move was not detected under name style")
        self.assertEqual(DONE.column_id, provider.pushed[0][1].column_id)

    def test_a_column_named_only_with_symbols_still_gets_a_folder(self) -> None:
        odd = RemoteColumn(column_id="9", name="???", group="todo")
        provider = FakeProvider([issue("K-1", odd)], columns=[odd])
        self.run_sync(provider)
        self.assertEqual(1, len(list((self.ws / "fake/projects/JPT").glob("*/K-1.md"))))


class LayoutTests(WorkspaceCase):
    def test_a_workspace_is_found_by_walking_up(self) -> None:
        layout.meta_dir(self.ws).mkdir(parents=True)
        layout.project_file(self.ws).write_text("{}", encoding="utf-8")
        deep = self.ws / "a" / "b" / "c"
        deep.mkdir(parents=True)
        self.assertEqual(self.ws.resolve(), layout.find_workspace(deep).resolve())  # type: ignore[union-attr]

    def test_outside_a_workspace_is_none(self) -> None:
        self.assertIsNone(layout.find_workspace(self.ws))

    def test_a_stray_file_is_not_read_as_a_card(self) -> None:
        """Only <project>/<column>/<file>.md is a card."""
        root = layout.project_dir(self.ws, "fake", PROJECT)
        stray = root / "notes.md"
        stray.parent.mkdir(parents=True)
        stray.write_text("x", encoding="utf-8")
        self.assertEqual("", layout.column_name_of(stray, self.ws, "fake", PROJECT))

    def test_github_style_owners_nest(self) -> None:
        project = RemoteProject(project_id="o/r", key="r", owner="o")
        self.assertEqual(
            self.ws / "github" / "projects" / "o" / "r",
            layout.project_dir(self.ws, "github", project),
        )


if __name__ == "__main__":
    unittest.main()


class ConfirmFlowTests(WorkspaceCase):
    """plan -> confirm -> push -> re-pull."""

    def _edited(self, provider: FakeProvider, key: str, new_title: str) -> None:
        path = self.file_for(key)
        path.write_text(
            path.read_text(encoding="utf-8").replace(f"title: Title for {key}", f"title: {new_title}"),
            encoding="utf-8",
        )

    def test_nothing_is_sent_until_the_plan_is_approved(self) -> None:
        provider = FakeProvider([issue("K-1", TODO)])
        self.run_sync(provider)
        self._edited(provider, "K-1", "Edited")

        report = self.run_sync(provider, confirm=lambda plan: False)
        self.assertEqual([], provider.pushed, "a declined plan still pushed")
        self.assertTrue(report.declined)

    def test_declining_still_pulls_so_the_files_stay_current(self) -> None:
        """A refused push must not also refuse to refresh the board."""
        provider = FakeProvider([issue("K-1", TODO)])
        self.run_sync(provider)
        self._edited(provider, "K-1", "Edited")

        provider._issues = [issue("K-1", TODO), issue("K-2", DONE)]
        self.run_sync(provider, confirm=lambda plan: False)
        self.assertTrue((self.ws / "fake/projects/JPT/done/K-2.md").is_file())

    def test_the_local_edit_survives_being_declined(self) -> None:
        """Declining is 'not now', not 'throw it away'."""
        provider = FakeProvider([issue("K-1", TODO)])
        self.run_sync(provider)
        self._edited(provider, "K-1", "Edited")
        self.run_sync(provider, confirm=lambda plan: False)

        seen: list[SyncPlan] = []
        self.run_sync(provider, confirm=record_and_accept(seen))
        self.assertEqual(1, len(seen[0].pushes), "the edit was lost when declined")
        self.assertEqual(1, len(provider.pushed))

    def test_the_plan_says_what_will_be_sent(self) -> None:
        provider = FakeProvider([issue("K-1", TODO)])
        self.run_sync(provider)
        self._edited(provider, "K-1", "Edited")

        seen: list[SyncPlan] = []
        self.run_sync(provider, confirm=record_and_accept(seen))
        plan = seen[0]
        self.assertEqual(1, len(plan.pushes))
        self.assertEqual("K-1", plan.pushes[0].key)
        self.assertEqual(("title",), plan.pushes[0].edit.touched())
        self.assertIn("K-1", plan.describe())

    def test_the_plan_separates_sendable_changes_from_blocked_conflicts(self) -> None:
        original = issue("K-1", TODO)
        clean = PendingPush(
            key="K-1",
            previous=original,
            edit=IssueEdit(title="Safe local title"),
        )
        conflict = PendingPush(
            key="K-2",
            previous=issue("K-2", TODO),
            remote=issue("K-2", TODO, title="Provider title"),
            edit=IssueEdit(title="Conflicting local title"),
            conflict=True,
        )
        plan = SyncPlan(
            pushes=[clean, conflict],
            creates=["A new story"],
            create_details=["signature"],
        )

        sendable = plan.describe_sendable()
        blocked = plan.describe_blocked()

        self.assertIn("READY TO SEND (2)", sendable)
        self.assertIn("CREATE (1)", sendable)
        self.assertIn("UPDATE (1)", sendable)
        self.assertNotIn("WILL", sendable)
        self.assertIn("K-1", sendable)
        self.assertNotIn("K-2", sendable)
        self.assertIn("BLOCKED (1)", blocked)
        self.assertNotIn("WILL", blocked)
        self.assertIn("K-2", blocked)
        self.assertIn("provider: Provider title", blocked)
        self.assertIn("local: Conflicting local title", blocked)

    def test_confirm_is_not_asked_when_there_is_nothing_to_send(self) -> None:
        provider = FakeProvider([issue("K-1", TODO)])
        self.run_sync(provider)
        asked: list[SyncPlan] = []
        self.run_sync(provider, confirm=record_and_accept(asked))
        self.assertEqual([], asked)

    def test_only_the_edited_issue_is_fetched_for_the_conflict_check(self) -> None:
        """The point of the per-issue check: a few small requests, not a re-read."""
        provider = FakeProvider([issue("K-1", TODO), issue("K-2", TODO), issue("K-3", TODO)])
        self.run_sync(provider)
        self._edited(provider, "K-2", "Edited")

        provider.fetched.clear()
        self.run_sync(provider, confirm=lambda plan: True)
        self.assertEqual(["K2"], provider.fetched)

    def test_a_remote_change_is_flagged_as_a_conflict_and_not_overwritten(self) -> None:
        original = issue("K-1", TODO)
        provider = FakeProvider([original])
        self.run_sync(provider)
        self._edited(provider, "K-1", "My title")

        # someone else renamed it on the tracker in the meantime
        provider._issues = [original.model_copy(update={"title": "Their title"})]

        seen: list[SyncPlan] = []
        report = self.run_sync(provider, confirm=record_and_accept(seen))
        self.assertTrue(seen[0].pushes[0].conflict)
        self.assertEqual([], provider.pushed, "a conflicting edit was pushed over their change")
        self.assertEqual(1, len(report.skipped))

    def test_a_conflict_can_be_pushed_when_explicitly_allowed(self) -> None:
        original = issue("K-1", TODO)
        provider = FakeProvider([original])
        self.run_sync(provider)
        self._edited(provider, "K-1", "My title")
        provider._issues = [original.model_copy(update={"title": "Their title"})]

        self.run_sync(provider, confirm=lambda plan: True, push_conflicts=True)
        self.assertEqual(["K-1"], [key for key, _ in provider.pushed])

    def test_accepting_the_provider_version_replaces_only_provider_fields(self) -> None:
        original = issue("K-1", TODO)
        provider = FakeProvider([original])
        self.run_sync(provider)
        path = self.file_for("K-1")
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace("title: Title for K-1", "title: My local title")
            .replace(markdown.NOTES_MARKER, f"{markdown.NOTES_MARKER}\n\nKeep this private note"),
            encoding="utf-8",
        )
        provider._issues = [original.model_copy(update={"title": "Provider title"})]

        report = self.run_sync(
            provider,
            confirm=lambda plan: True,
            accept_remote_conflicts=True,
        )

        self.assertEqual([], provider.pushed)
        self.assertEqual(["K-1"], report.accepted)
        refreshed = self.file_for("K-1").read_text(encoding="utf-8")
        self.assertIn("title: Provider title", refreshed)
        self.assertIn("Keep this private note", refreshed)

    def test_an_unverifiable_provider_is_reported_not_assumed_safe(self) -> None:
        """No single-issue endpoint means 'cannot check', not 'no conflict'."""
        provider = FakeProvider([issue("K-1", TODO)], can_get_issue=False)
        self.run_sync(provider)
        self._edited(provider, "K-1", "Edited")

        seen: list[SyncPlan] = []
        self.run_sync(provider, confirm=record_and_accept(seen))
        self.assertTrue(seen[0].pushes[0].unchecked)
        self.assertFalse(seen[0].pushes[0].conflict)
        self.assertIn("could not check", seen[0].pushes[0].describe())

    def test_an_unverifiable_provider_is_not_sent_without_force(self) -> None:
        provider = FakeProvider([issue("K-1", TODO)], can_get_issue=False)
        self.run_sync(provider)
        self._edited(provider, "K-1", "Edited")

        report = self.run_sync(provider, confirm=lambda plan: True)

        self.assertEqual([], provider.pushed)
        self.assertIn("could not check", report.skipped[0][1])

    def test_remote_change_to_another_field_does_not_block_a_local_title(self) -> None:
        original = issue("K-1", TODO)
        provider = FakeProvider([original])
        self.run_sync(provider)
        self._edited(provider, "K-1", "My title")
        provider._issues = [original.model_copy(update={"body": "Their body"})]

        report = self.run_sync(provider, confirm=lambda plan: True)

        self.assertEqual([], report.skipped)
        self.assertEqual(1, len(provider.pushed))

    def test_the_repull_is_not_served_from_a_stale_cache(self) -> None:
        """The trap: a cached issue list would put the pre-push state back."""
        provider = FakeProvider([issue("K-1", TODO)])
        self.run_sync(provider)
        self._edited(provider, "K-1", "Edited")
        provider.refreshed = False
        self.run_sync(provider, confirm=lambda plan: True)
        self.assertTrue(provider.refreshed, "the issue cache was not bypassed after pushing")
