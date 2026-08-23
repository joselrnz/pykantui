"""The push path, hard.

Two ways a change reaches a tracker, and they must behave identically:

* **the markdown** -- edit a file, run a sync, confirm
* **the UI** -- move a card on the board, which first updates Markdown

Both reach ``Provider.update_issue`` / ``Provider.move_issue`` only through an
approved Sync. These tests drive each route and check the resulting request.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import ColumnGroup, IssueEdit, RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace import layout, markdown
from pykantui.workspace.sync import sync

TODO = RemoteColumn(column_id="c-todo", name="To Do", position=0, group=ColumnGroup.TODO)
DOING = RemoteColumn(column_id="c-doing", name="In Progress", position=1, group=ColumnGroup.STARTED)
REVIEW = RemoteColumn(column_id="c-review", name="In Review", position=2, group=ColumnGroup.REVIEW)
DONE = RemoteColumn(column_id="c-done", name="Done", position=3, group=ColumnGroup.DONE)
COLUMNS = [TODO, DOING, REVIEW, DONE]

PROJECT = RemoteProject(project_id="P1", key="JPT", name="jira-project-test")

ALL_WRITABLE = (
    "title",
    "body",
    "column_id",
    "assignee",
    "labels",
    "components",
    "due_date",
    "priority",
    "issue_type",
)


class RecordingProvider(Provider):
    """Records every call instead of making one."""

    spec = ProviderSpec(
        name="rec",
        label="Recorder",
        auth_fields=(ProviderField(name="token", label="T", kind=FieldKind.SECRET),),
        capabilities=Capabilities(move_issues=True, writable_fields=ALL_WRITABLE),
    )

    def __init__(self, issues: list[RemoteIssue], **kw: object) -> None:
        super().__init__({}, {})
        self._issues = list(issues)
        self.updates: list[tuple[str, IssueEdit]] = []
        self.moves: list[tuple[str, str]] = []
        self.fetches: list[str] = []
        self.fail_on: set[str] = set()

    def verify(self) -> RemoteUser:
        return RemoteUser(display_name="rec")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return COLUMNS

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(self._issues)

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        self.fetches.append(issue.issue_id)
        return next((i for i in self._issues if i.issue_id == issue.issue_id), None)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)
        if issue.display_key() in self.fail_on:
            raise ProviderError(f"{issue.display_key()} was refused")
        self.updates.append((issue.display_key(), edit))
        self._apply(issue, edit)

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        if issue.display_key() in self.fail_on:
            raise ProviderError(f"{issue.display_key()} cannot move there")
        self.moves.append((issue.display_key(), column.column_id))
        self._apply(issue, IssueEdit(column_id=column.column_id))

    def _apply(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """Behave like a real tracker: accept the change and serve it back."""
        changes = {name: getattr(edit, name) for name in edit.touched() if getattr(edit, name) is not None}
        for index, existing in enumerate(self._issues):
            if existing.issue_id == issue.issue_id:
                self._issues[index] = existing.model_copy(update=changes)


def issue(key: str, column: RemoteColumn, **kw: object) -> RemoteIssue:
    base: dict[str, object] = {
        "issue_id": f"id-{key}",
        "key": key,
        "title": f"Title {key}",
        "column_id": column.column_id,
        "status": column.name,
        "body": f"Body {key}",
    }
    base.update(kw)
    return RemoteIssue(**base)  # type: ignore[arg-type]


class PushCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def sync(self, provider: RecordingProvider, **kw: object) -> object:
        options: dict[str, object] = {"commit": False, "confirm": lambda plan: True}
        options.update(kw)
        return sync(self.ws, provider, PROJECT, **options)  # type: ignore[arg-type]

    def file_for(self, key: str) -> Path:
        found = list(self.ws.rglob(f"{key}.md"))
        self.assertEqual(1, len(found), f"expected one {key}.md, found {found}")
        return found[0]

    def edit_front(self, key: str, line: str, replacement: str) -> None:
        path = self.file_for(key)
        text = path.read_text(encoding="utf-8")
        self.assertIn(line, text, f"{line!r} not in {key}.md")
        path.write_text(text.replace(line, replacement), encoding="utf-8")

    def last_edit(self, provider: RecordingProvider) -> IssueEdit:
        self.assertTrue(provider.updates, "nothing was pushed")
        return provider.updates[-1][1]


class FieldPushTests(PushCase):
    """Every editable field, edited in markdown and pushed."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(self.provider)
        self.provider.updates.clear()

    def test_title(self) -> None:
        self.edit_front("K-1", "title: Title K-1", "title: A new title")
        self.sync(self.provider)
        self.assertEqual("A new title", self.last_edit(self.provider).title)

    def test_body(self) -> None:
        path = self.file_for("K-1")
        path.write_text(path.read_text(encoding="utf-8").replace("Body K-1", "Rewritten body"), encoding="utf-8")
        self.sync(self.provider)
        self.assertEqual("Rewritten body", self.last_edit(self.provider).body)

    def test_assignee(self) -> None:
        self.edit_front("K-1", "column: to-do", "column: to-do\nassignee: alex")
        self.sync(self.provider)
        self.assertEqual("alex", self.last_edit(self.provider).assignee)

    def test_priority(self) -> None:
        self.edit_front("K-1", "column: to-do", "column: to-do\npriority: High")
        self.sync(self.provider)
        self.assertEqual("High", self.last_edit(self.provider).priority)

    def test_labels(self) -> None:
        self.edit_front("K-1", "column: to-do", "column: to-do\nlabels: [alpha, beta]")
        self.sync(self.provider)
        self.assertEqual(("alpha", "beta"), self.last_edit(self.provider).labels)

    def test_issue_type(self) -> None:
        self.edit_front("K-1", "column: to-do", "column: to-do\ntype: Bug")
        self.sync(self.provider)
        self.assertEqual("Bug", self.last_edit(self.provider).issue_type)

    def test_components(self) -> None:
        self.edit_front("K-1", "column: to-do", "column: to-do\ncomponents: [API, Platform]")
        self.sync(self.provider)
        self.assertEqual(("API", "Platform"), self.last_edit(self.provider).components)

    def test_due_date(self) -> None:
        self.edit_front("K-1", "column: to-do", "column: to-do\ndue: 2026-12-25")
        self.sync(self.provider)
        self.assertEqual(date(2026, 12, 25), self.last_edit(self.provider).due_date)

    def test_column_by_moving_the_file(self) -> None:
        source = self.file_for("K-1")
        target = self.ws / "rec/projects/JPT/done/K-1.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        self.sync(self.provider)
        self.assertEqual(DONE.column_id, self.last_edit(self.provider).column_id)

    def test_two_fields_at_once_go_in_one_request(self) -> None:
        """One PUT, not one per field -- a half-applied edit must be impossible."""
        self.edit_front("K-1", "title: Title K-1", "title: Renamed\npriority: Low")
        self.sync(self.provider)
        self.assertEqual(1, len(self.provider.updates))
        edit = self.last_edit(self.provider)
        self.assertEqual({"title", "priority"}, set(edit.touched()))

    def test_an_untouched_file_pushes_nothing(self) -> None:
        self.sync(self.provider)
        self.assertEqual([], self.provider.updates)


class ClearingTests(PushCase):
    """Emptying a field is a different request from leaving it alone."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider(
            [issue("K-1", TODO, priority="High", due_date=date(2026, 8, 20), labels=("a",))]
        )
        self.sync(self.provider)
        self.provider.updates.clear()

    def test_deleting_a_due_date_line_clears_it(self) -> None:
        self.edit_front("K-1", "due: '2026-08-20'\n", "")
        self.sync(self.provider)
        edit = self.last_edit(self.provider)
        self.assertIn("due_date", edit.cleared)
        self.assertIsNone(edit.due_date)

    def test_deleting_the_labels_line_clears_them(self) -> None:
        self.edit_front("K-1", "labels: [a]\n", "")
        self.sync(self.provider)
        self.assertIn("labels", self.last_edit(self.provider).cleared)

    def test_leaving_a_field_alone_sends_nothing_for_it(self) -> None:
        self.edit_front("K-1", "title: Title K-1", "title: Renamed")
        self.sync(self.provider)
        edit = self.last_edit(self.provider)
        self.assertEqual((), edit.cleared)
        self.assertIsNone(edit.due_date, "an untouched due date was resent")


class PartialFailureTests(PushCase):
    """One stuck issue must not abandon the rest."""

    def test_a_refused_push_does_not_stop_the_others(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO), issue("K-2", TODO), issue("K-3", TODO)])
        self.sync(provider)
        for key in ("K-1", "K-2", "K-3"):
            self.edit_front(key, f"title: Title {key}", f"title: Edited {key}")

        provider.fail_on = {"K-2"}
        report = self.sync(provider)
        self.assertEqual({"K-1", "K-3"}, {key for key, _ in provider.updates})
        self.assertEqual(1, len(report.skipped))  # type: ignore[attr-defined]
        self.assertIn("K-2", report.skipped[0][0])  # type: ignore[attr-defined]

    def test_a_refused_edit_keeps_its_file(self) -> None:
        """The file is the only copy of a change that did not land."""
        provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(provider)
        self.edit_front("K-1", "title: Title K-1", "title: Precious")
        provider.fail_on = {"K-1"}
        self.sync(provider)
        self.assertIn("title: Precious", self.file_for("K-1").read_text(encoding="utf-8"))

    def test_an_unwritable_field_is_reported_with_its_name(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO)])
        provider.spec = provider.spec.model_copy(  # type: ignore[misc]
            update={"capabilities": Capabilities(move_issues=True, writable_fields=("body",))}
        )
        self.sync(provider)
        self.edit_front("K-1", "title: Title K-1", "title: Nope")
        report = self.sync(provider)
        self.assertEqual([], provider.updates)
        self.assertIn("title", report.skipped[0][1])  # type: ignore[attr-defined]


class IdempotencyTests(PushCase):
    """A push must not repeat itself on the next run."""

    def test_the_same_edit_is_not_pushed_twice(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(provider)
        self.edit_front("K-1", "title: Title K-1", "title: Once")

        self.sync(provider)
        self.assertEqual(1, len(provider.updates))
        self.sync(provider)
        self.assertEqual(1, len(provider.updates), "the same edit was pushed again")

    def test_the_file_matches_the_tracker_after_a_push(self) -> None:
        """Written from the tracker's answer, not from what we hoped we sent."""
        provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(provider)
        self.edit_front("K-1", "title: Title K-1", "title: Accepted")
        self.sync(provider)
        self.assertIn("title: Accepted", self.file_for("K-1").read_text(encoding="utf-8"))

    def test_three_syncs_in_a_row_change_nothing(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO), issue("K-2", DOING)])
        self.sync(provider)
        for _ in range(3):
            report = self.sync(provider)
            self.assertEqual(0, report.total_changes(), report.summary())  # type: ignore[attr-defined]
            self.assertEqual([], provider.updates)


class UnicodeAndEdgeTests(PushCase):
    def test_an_emoji_title_round_trips(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(provider)
        self.edit_front("K-1", "title: Title K-1", "title: Ship it 🚀 now")
        self.sync(provider)
        self.assertEqual("Ship it 🚀 now", self.last_edit(provider).title)

    def test_a_colon_in_a_title_round_trips(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(provider)
        self.edit_front("K-1", "title: Title K-1", "title: 'Fix: the thing'")
        self.sync(provider)
        self.assertEqual("Fix: the thing", self.last_edit(provider).title)

    def test_a_multi_paragraph_body_round_trips(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(provider)
        path = self.file_for("K-1")
        body = "# Heading\n\nOne.\n\n- a\n- b\n\n```py\nx = 1\n```"
        path.write_text(path.read_text(encoding="utf-8").replace("Body K-1", body), encoding="utf-8")
        self.sync(provider)
        pushed = self.last_edit(provider).body or ""
        self.assertIn("```py", pushed)
        self.assertIn("- a", pushed)

    def test_a_body_that_looks_like_frontmatter_round_trips(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(provider)
        path = self.file_for("K-1")
        path.write_text(path.read_text(encoding="utf-8").replace("Body K-1", "---\nkey: fake\n---"), encoding="utf-8")
        self.sync(provider)
        self.assertIn("key: fake", self.last_edit(provider).body or "")


class UiPushTests(PushCase):
    """The other route: a card moved on the board."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider([issue("K-1", TODO), issue("K-2", DOING)])
        self.sync(self.provider)
        self.backend = ProviderBackend(self.ws, self.provider, PROJECT)

    def test_the_board_shows_the_columns_the_tracker_has(self) -> None:
        self.assertEqual(
            ["To Do", "In Progress", "In Review", "Done"], [column.name for column in self.backend.get_columns()]
        )

    def test_the_board_shows_the_cards_from_the_files(self) -> None:
        keys = {task.metadata["key"] for task in self.backend.get_tasks()}
        self.assertEqual({"K-1", "K-2"}, keys)

    def test_moving_a_card_does_not_push_until_sync(self) -> None:
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in self.backend.get_columns() if c.name == "Done")
        result = self.backend.move_task(task, done.column_id)
        self.assertTrue(result.ok, result.message)
        self.assertEqual([], self.provider.moves)
        self.assertEqual([], self.provider.updates)

    def test_moving_a_card_moves_its_file(self) -> None:
        """The UI and the files must never disagree about where a card is."""
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in self.backend.get_columns() if c.name == "Done")
        self.backend.move_task(task, done.column_id)
        self.assertTrue((self.ws / "rec/projects/JPT/done/K-1.md").is_file())
        self.assertFalse((self.ws / "rec/projects/JPT/to-do/K-1.md").exists())

    def test_a_refused_provider_move_keeps_the_local_intent(self) -> None:
        """A later provider refusal must not discard the local Markdown."""
        self.provider.fail_on = {"K-1"}
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in self.backend.get_columns() if c.name == "Done")
        result = self.backend.move_task(task, done.column_id)
        self.assertTrue(result.ok, result.message)
        report = self.sync(self.provider)
        self.assertEqual(1, len(report.skipped))  # type: ignore[attr-defined]
        self.assertTrue((self.ws / "rec/projects/JPT/done/K-1.md").is_file())

    def test_a_ui_move_survives_the_next_sync(self) -> None:
        """The round trip: move in the UI, then sync, and it stays moved."""
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in self.backend.get_columns() if c.name == "Done")
        self.backend.move_task(task, done.column_id)

        self.sync(self.provider)
        self.assertTrue((self.ws / "rec/projects/JPT/done/K-1.md").is_file())

    def test_a_ui_move_is_sent_once_by_sync(self) -> None:
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in self.backend.get_columns() if c.name == "Done")
        self.backend.move_task(task, done.column_id)

        self.provider.updates.clear()
        self.sync(self.provider)
        self.assertEqual(1, len(self.provider.updates))
        self.sync(self.provider)
        self.assertEqual(1, len(self.provider.updates), "a second sync sent the move twice")

    def test_notes_survive_a_ui_move(self) -> None:
        path = self.file_for("K-1")
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\nmy notes\n", encoding="utf-8")
        backend = ProviderBackend(self.ws, self.provider, PROJECT)

        task = next(t for t in backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in backend.get_columns() if c.name == "Done")
        backend.move_task(task, done.column_id)
        self.assertEqual("my notes", markdown.read(self.file_for("K-1")).notes)

    def test_editing_a_card_writes_markdown_not_the_provider(self) -> None:
        """Text edits join the plan-and-confirm path instead of bypassing it."""
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1").model_copy(
            update={"title": "Local title"}
        )
        result = self.backend.update_task(task)
        self.assertTrue(result.ok, result.message)
        self.assertEqual([], self.provider.updates)
        self.assertIn("title: Local title", self.file_for("K-1").read_text(encoding="utf-8"))

    def test_the_board_offers_no_column_editor(self) -> None:
        """Columns belong to the tracker; local edits would be discarded."""
        self.assertIsNone(self.backend.board_config())

    def test_a_read_only_provider_refuses_the_move_politely(self) -> None:
        self.provider.spec = self.provider.spec.model_copy(  # type: ignore[misc]
            update={"capabilities": Capabilities(move_issues=False)}
        )
        task = self.backend.get_tasks()[0]
        done = next(c for c in self.backend.get_columns() if c.name == "Done")
        result = self.backend.move_task(task, done.column_id)
        self.assertFalse(result.ok)
        self.assertIn("cannot move", result.message)


class StableTaskIdentityTests(PushCase):
    """A recycled UI row number must never identify a different provider card."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider([issue("K-1", TODO), issue("K-2", TODO)])
        self.sync(self.provider)
        self.backend = ProviderBackend(self.ws, self.provider, PROJECT)

        tasks = sorted(self.backend.get_tasks(), key=lambda task: task.task_id)
        self.stale = tasks[0]
        self.survivor = tasks[1]
        self.file_for(str(self.stale.metadata["key"])).unlink()
        self.backend.reload_local()

        replacement = self.backend.get_task_by_id(self.stale.task_id)
        self.assertIsNotNone(replacement)
        assert replacement is not None
        self.assertEqual(self.survivor.metadata["id"], replacement.metadata["id"])
        self.assertNotEqual(self.stale.metadata["id"], replacement.metadata["id"])

    def test_stale_row_number_cannot_update_the_surviving_markdown_card(self) -> None:
        survivor_path = self.file_for(str(self.survivor.metadata["key"]))
        before = survivor_path.read_text(encoding="utf-8")
        stale_edit = self.stale.model_copy(update={"title": "stale title must not cross cards"})

        result = self.backend.update_task(stale_edit)

        self.assertFalse(result.ok, result.message)
        self.assertEqual(before, survivor_path.read_text(encoding="utf-8"))

    def test_stale_row_number_cannot_move_the_surviving_markdown_card(self) -> None:
        survivor_key = str(self.survivor.metadata["key"])
        survivor_path = self.file_for(survivor_key)
        done = next(column for column in self.backend.get_columns() if column.name == "Done")

        result = self.backend.move_task(self.stale, done.column_id)

        self.assertFalse(result.ok, result.message)
        self.assertTrue(survivor_path.is_file())
        self.assertFalse((self.ws / f"rec/projects/JPT/done/{survivor_key}.md").exists())

    def test_stale_row_number_is_not_editable_after_reload(self) -> None:
        self.assertFalse(self.backend.can_edit_task(self.stale))


class BothRoutesAgreeTests(PushCase):
    """A move made in the UI and one made by dragging a file must look the same."""

    def test_the_same_provider_call_comes_out_of_both(self) -> None:
        ui = RecordingProvider([issue("K-1", TODO)])
        self.sync(ui)
        backend = ProviderBackend(self.ws, ui, PROJECT)
        task = backend.get_tasks()[0]
        done = next(c for c in backend.get_columns() if c.name == "Done")
        backend.move_task(task, done.column_id)
        self.sync(ui)
        ui_target = ui.updates[0][1].column_id

        with tempfile.TemporaryDirectory() as other:
            self.ws = Path(other)
            files = RecordingProvider([issue("K-1", TODO)])
            self.sync(files)
            source = self.file_for("K-1")
            target = self.ws / "rec/projects/JPT/done/K-1.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            self.sync(files)
            file_target = files.updates[0][1].column_id

        self.assertEqual(ui_target, file_target)
        self.assertEqual(DONE.column_id, ui_target)


if __name__ == "__main__":
    unittest.main()


class UiMoveSnapshotTests(PushCase):
    """A local-first move keeps the old baseline until Sync accepts it."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(self.provider)
        self.backend = ProviderBackend(self.ws, self.provider, PROJECT)

    def _move_in_ui(self, to_name: str) -> None:
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1")
        column = next(c for c in self.backend.get_columns() if c.name == to_name)
        result = self.backend.move_task(task, column.column_id)
        self.assertTrue(result.ok, result.message)

    def test_a_ui_move_does_not_advance_the_snapshot_before_sync(self) -> None:
        from pykantui.workspace.state import SyncState

        self._move_in_ui("Done")
        snapshot = SyncState.load(layout.state_file(self.ws)).get("id-K-1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(TODO.column_id, snapshot.column_id)  # type: ignore[union-attr]

    def test_an_edit_after_a_ui_move_is_not_a_conflict(self) -> None:
        """The exact sequence that failed in the live run."""
        self._move_in_ui("Done")

        path = self.file_for("K-1")
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Renamed"), encoding="utf-8"
        )

        self.provider.updates.clear()
        report = self.sync(self.provider)
        self.assertEqual([], report.skipped, f"a self-inflicted conflict: {report.skipped}")  # type: ignore[attr-defined]
        self.assertEqual(["K-1"], [key for key, _ in self.provider.updates])

    def test_a_genuine_remote_change_is_still_a_conflict(self) -> None:
        """The fix must not blind the conflict check to real changes."""
        self._move_in_ui("Done")
        # someone else renames it on the tracker
        self.provider._issues = [i.model_copy(update={"title": "Theirs"}) for i in self.provider._issues]

        path = self.file_for("K-1")
        path.write_text(path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Mine"), encoding="utf-8")

        self.provider.updates.clear()
        report = self.sync(self.provider)
        self.assertEqual([], self.provider.updates, "a real conflict was pushed over")
        self.assertEqual(1, len(report.skipped))  # type: ignore[attr-defined]


class OfferedActionsTests(PushCase):
    """The board must not offer what the backend will refuse.

    Found by asking what right-clicking a column actually does: "New card
    here" was on the menu, and picking it failed. An action that cannot work
    should not be offered.
    """

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider([issue("K-1", TODO)])
        self.sync(self.provider)
        self.backend = ProviderBackend(self.ws, self.provider, PROJECT)

    def test_creating_is_not_offered(self) -> None:
        self.assertFalse(self.backend.writable, "the board would offer 'New card here'")

    def test_creating_still_refuses_if_something_calls_it_anyway(self) -> None:
        from pykantui.models import Task

        result = self.backend.create_task(Task(task_id=99, title="x", column_id=1))
        self.assertFalse(result.ok)
        self.assertIn("created in", result.message)

    def test_moving_is_unaffected_by_that_flag(self) -> None:
        """writable gates create/edit/delete, never the local move."""
        task = next(t for t in self.backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in self.backend.get_columns() if c.name == "Done")
        result = self.backend.move_task(task, done.column_id)
        self.assertTrue(result.ok, result.message)
        self.assertEqual([], self.provider.moves)
        self.assertEqual("edited", result.task.metadata["sync_status"] if result.task else "")

    def test_the_move_warning_names_the_tracker(self) -> None:
        """With writable False the confirmation says where the write goes."""
        self.assertEqual("Recorder", self.backend.display_kind())


class SyncStatusTests(PushCase):
    """The dot on a card: which cards hold work that has not been sent."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider([issue("K-1", TODO), issue("K-2", DOING)])
        self.sync(self.provider)

    def status(self, key: str) -> str:
        backend = ProviderBackend(self.ws, self.provider, PROJECT)
        task = next(t for t in backend.get_tasks() if t.metadata["key"] == key)
        return str(task.metadata["sync_status"])

    def test_an_untouched_card_is_synced(self) -> None:
        self.assertEqual("synced", self.status("K-1"))

    def test_an_edited_card_shows_an_unsent_edit(self) -> None:
        """The state the board previously could not show at all."""
        self.edit_front("K-1", "title: Title K-1", "title: Changed")
        self.assertEqual("edited", self.status("K-1"))
        self.assertEqual("synced", self.status("K-2"), "the edit leaked to another card")

    def test_a_moved_file_counts_as_edited(self) -> None:
        source = self.ws / "rec/projects/JPT/to-do/K-1.md"
        target = self.ws / "rec/projects/JPT/done/K-1.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        self.assertEqual("edited", self.status("K-1"))

    def test_pushing_returns_it_to_synced(self) -> None:
        self.edit_front("K-1", "title: Title K-1", "title: Changed")
        self.sync(self.provider)
        self.assertEqual("synced", self.status("K-1"))

    def test_a_declined_push_leaves_it_edited(self) -> None:
        """The dot has to keep showing work that is still waiting."""
        self.edit_front("K-1", "title: Title K-1", "title: Changed")
        self.sync(self.provider, confirm=lambda plan: False)
        self.assertEqual("edited", self.status("K-1"))

    def test_a_ui_move_marks_the_card_as_unsent(self) -> None:
        backend = ProviderBackend(self.ws, self.provider, PROJECT)
        task = next(t for t in backend.get_tasks() if t.metadata["key"] == "K-1")
        done = next(c for c in backend.get_columns() if c.name == "Done")
        backend.move_task(task, done.column_id)
        self.assertEqual("edited", self.status("K-1"))

    def test_the_markers_are_distinguishable(self) -> None:
        from pykantui.workspace.status import SyncStatus

        markers = {status.marker for status in SyncStatus}
        self.assertEqual(len(SyncStatus), len(markers), "two states share a marker")

    def test_the_header_summary_names_only_what_needs_attention(self) -> None:
        from pykantui.workspace.status import SyncStatus, summarise

        self.assertEqual("all synced", summarise([SyncStatus.SYNCED, SyncStatus.SYNCED]))
        self.assertEqual("1 unsent edit", summarise([SyncStatus.SYNCED, SyncStatus.EDITED]))
        self.assertEqual("1 invalid Markdown", summarise([SyncStatus.SYNCED, SyncStatus.INVALID]))

    def test_a_board_with_no_tracker_shows_no_dot(self) -> None:
        """A local JSON board has nothing to be out of step with, so a marker
        that never changes would be noise."""
        from types import SimpleNamespace

        from pykantui.models import Task
        from pykantui.tui.widgets.card import TaskCard

        plain = SimpleNamespace(task_=Task(task_id=1, title="x", column_id=1))
        self.assertIsNone(TaskCard.sync_status(plain))  # type: ignore[arg-type]

    def test_an_unrecognised_status_is_ignored_not_fatal(self) -> None:
        """A file written by a future version must not break the board."""
        from types import SimpleNamespace

        from pykantui.models import Task
        from pykantui.tui.widgets.card import TaskCard

        odd = SimpleNamespace(task_=Task(task_id=1, title="x", column_id=1, metadata={"sync_status": "sideways"}))
        self.assertIsNone(TaskCard.sync_status(odd))  # type: ignore[arg-type]


class StatusColourTests(unittest.TestCase):
    """Colour is what makes the dot readable at a glance rather than read."""

    def test_synced_spends_no_colour(self) -> None:
        """The normal state must not compete with the ones that want something.

        It was briefly marked `$text-muted`, which is worse than nothing: that
        variable does not resolve in content markup, so it fell back to
        full-strength white and made the quietest state the brightest thing on
        the card.
        """
        from pykantui.workspace.status import SyncStatus

        self.assertEqual("", SyncStatus.SYNCED.colour)
        self.assertNotIn("[", SyncStatus.SYNCED.markup())

    def test_the_states_that_want_something_are_coloured(self) -> None:
        from pykantui.workspace.status import SyncStatus

        for status in (SyncStatus.EDITED, SyncStatus.CONFLICT, SyncStatus.NEW, SyncStatus.INVALID):
            with self.subTest(status=status):
                self.assertTrue(status.colour.startswith("$"), "not a theme variable")
                self.assertIn(status.colour, status.markup())

    def test_colours_are_theme_variables_not_literals(self) -> None:
        """A hardcoded green vanishes on a light background."""
        from pykantui.workspace.status import SyncStatus

        for status in SyncStatus:
            if status.colour:
                self.assertTrue(status.colour.startswith("$"), f"{status} uses a literal colour")

    def test_severity_is_ordered_the_way_a_reader_expects(self) -> None:
        from pykantui.workspace.status import SyncStatus

        self.assertEqual("$warning", SyncStatus.EDITED.colour)
        self.assertEqual("$error", SyncStatus.CONFLICT.colour)
        self.assertEqual("$error", SyncStatus.INVALID.colour)

    def test_markup_is_balanced(self) -> None:
        """An unclosed tag would swallow the rest of the metadata line."""
        from pykantui.workspace.status import SyncStatus

        for status in SyncStatus:
            markup = status.markup()
            self.assertEqual(markup.count("["), markup.count("]"))


class ReachableStateTests(PushCase):
    """Every state the board defines must be possible to actually see.

    Two were not, and both were found by trying to render them:

    * ``NEW`` -- a file with no snapshot was *skipped*, so a card that had
      never synced was invisible. That is exactly the state a locally created
      card starts in, so building creation would have produced files nobody
      could see.
    * ``CONFLICT`` -- nothing ever recorded one, so it was defined, coloured
      and tested but impossible to reach.
    """

    def setUp(self) -> None:
        super().setUp()
        self.provider = RecordingProvider([issue("K-1", TODO), issue("K-2", DOING)])
        self.sync(self.provider)

    def statuses(self) -> dict[str, str]:
        backend = ProviderBackend(self.ws, self.provider, PROJECT)
        return {t.metadata["key"]: str(t.metadata["sync_status"]) for t in backend.get_tasks()}

    def test_a_never_synced_card_is_visible(self) -> None:
        (self.ws / "rec/projects/JPT/to-do/K-9.md").write_text(
            '---\nkey: K-9\nid: "999"\ntitle: Brand new\nstatus: To Do\ncolumn: to-do\n---\n\n'
            "<!-- pykantui:source -->\n\n<!-- pykantui:notes -->\n",
            encoding="utf-8",
        )
        found = self.statuses()
        self.assertIn("K-9", found, "a card with no snapshot vanished from the board")
        self.assertEqual("new", found["K-9"])

    def test_an_unsynced_card_keeps_its_title(self) -> None:
        """Shown from its own frontmatter, since there is nothing else."""
        (self.ws / "rec/projects/JPT/to-do/K-9.md").write_text(
            '---\nkey: K-9\nid: "999"\ntitle: Brand new\nstatus: To Do\ncolumn: to-do\n---\n\n'
            "<!-- pykantui:source -->\n\n<!-- pykantui:notes -->\n",
            encoding="utf-8",
        )
        backend = ProviderBackend(self.ws, self.provider, PROJECT)
        task = next(t for t in backend.get_tasks() if t.metadata["key"] == "K-9")
        self.assertIn("Brand new", task.title)

    def test_a_recorded_conflict_is_shown(self) -> None:
        from pykantui.workspace.state import SyncState

        self.edit_front("K-1", "title: Title K-1", "title: Mine")
        state = SyncState.load(layout.state_file(self.ws))
        state.mark_conflicts({"id-K-1"})
        state.save(layout.state_file(self.ws))

        self.assertEqual("conflict", self.statuses()["K-1"])

    def test_a_resolved_conflict_stops_being_reported(self) -> None:
        """mark_conflicts replaces rather than accumulates."""
        from pykantui.workspace.state import SyncState

        state = SyncState.load(layout.state_file(self.ws))
        state.mark_conflicts({"id-K-1"})
        state.save(layout.state_file(self.ws))
        state = SyncState.load(layout.state_file(self.ws))
        state.mark_conflicts(set())
        state.save(layout.state_file(self.ws))
        self.assertEqual(set(), SyncState.load(layout.state_file(self.ws)).conflicts)

    def test_the_sync_records_what_it_found(self) -> None:
        from pykantui.workspace.state import SyncState

        original = issue("K-1", TODO)
        self.edit_front("K-1", "title: Title K-1", "title: Mine")
        self.provider._issues = [
            original.model_copy(update={"title": "Theirs"}),
            issue("K-2", DOING),
        ]
        self.sync(self.provider)
        self.assertIn("id-K-1", SyncState.load(layout.state_file(self.ws)).conflicts)

    def test_action_categories_have_distinct_colours(self) -> None:
        """New, edited, and unsafe states must remain visually distinct.

        Invalid Markdown and a provider conflict intentionally share the red
        danger category; their markers and labels explain the different fix.
        """
        from pykantui.workspace.status import SyncStatus

        categories = (SyncStatus.NEW, SyncStatus.EDITED, SyncStatus.CONFLICT)
        colours = [status.colour for status in categories]
        self.assertEqual(len(colours), len(set(colours)), "two action categories share a colour")
        self.assertEqual(SyncStatus.CONFLICT.colour, SyncStatus.INVALID.colour)
