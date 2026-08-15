"""Drafting a story locally, and the two ways that went wrong.

A draft is a file with no counterpart on the tracker. That one property broke
both halves of the sync in opposite directions:

* the prune deleted every draft, because "not in the fetched issues" is exactly
  how it recognises a deleted issue -- so ``kbn sync --dry-run``, the command
  whose entire purpose is to change nothing, destroyed the lot;
* and the create never ran at all, because nothing called it, so a confirmed
  sync reported "no changes" and quietly left the drafts sitting there.

Both are covered here by behaviour rather than by calling the helpers directly:
these tests should still fail if someone reorganises the internals and
reintroduces either symptom.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pykantui.api.errors import PayloadError
from pykantui.commands.new import DRAFT_PREFIX, is_draft, write_draft
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError, TransportError, UnsupportedError
from pykantui.tracker.models import (
    CommentDraft,
    IssueDraft,
    IssueEdit,
    IssueType,
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.spec import Capabilities, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project
from pykantui.workspace.state import SyncState
from pykantui.workspace.sync import SyncPlan, SyncReport, sync

TODO = RemoteColumn(column_id="c-todo", name="To Do", position=0, group="todo")
DOING = RemoteColumn(column_id="c-doing", name="In Progress", position=1, group="started")
COLUMNS = [TODO, DOING]

PROJECT = RemoteProject(project_id="P1", key="JPT", name="jira-project-test")


class CreatingProvider(Provider):
    """A tracker that accepts creates and hands back its own keys."""

    spec = ProviderSpec(
        name="rec",
        label="Recorder",
        auth_fields=(ProviderField(name="token", label="T", kind=FieldKind.SECRET),),
        capabilities=Capabilities(
            move_issues=True,
            create_issues=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "issue_type"),
        ),
    )

    def __init__(self, issues: list[RemoteIssue] | None = None) -> None:
        super().__init__({}, {})
        self._issues = list(issues or [])
        self.created: list[IssueDraft] = []
        self.refuse = False
        self._next = 100

        #: Deliberately in Jira's awkward order -- sub-task first, then the
        #: container, then the ordinary types -- so "first in the list" is the
        #: wrong answer and a test that passes proves the level is being read.
        self.types = [
            IssueType(type_id="1", name="Subtask", subtask=True, level=-1),
            IssueType(type_id="2", name="Epic", level=1),
            IssueType(type_id="3", name="Task", level=0),
            IssueType(type_id="4", name="Story", level=0),
        ]
        self.type_calls = 0

    def verify(self) -> RemoteUser:
        return RemoteUser(display_name="rec")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return COLUMNS

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(list(self._issues))

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        return next((i for i in self._issues if i.issue_id == issue.issue_id), None)

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        self.type_calls += 1
        return list(self.types)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        if self.refuse:
            raise ProviderError("the tracker refused this one")
        self.created.append(draft)
        self._next += 1
        made = RemoteIssue(
            issue_id=f"id-{self._next}",
            key=f"JPT-{self._next}",
            title=draft.title,
            column_id=draft.column_id or TODO.column_id,
            status=draft.column_name or TODO.name,
            body=draft.body,
            issue_type=draft.issue_type,
            labels=draft.labels,
            # A real tracker records who the issue was created for. Dropping it
            # here would make every test think a new issue is unassigned.
            assignee=draft.assignee,
            assignee_ids=draft.assignee_ids,
        )
        self._issues.append(made)
        return made


class NoCreateProvider(CreatingProvider):
    """Same, but the tracker has no create endpoint."""

    spec = ProviderSpec(
        name="rec-nocreate",
        label="Read-mostly",
        auth_fields=(ProviderField(name="token", label="T", kind=FieldKind.SECRET),),
        capabilities=Capabilities(create_issues=False, writable_fields=("title",)),
    )


class DueCreatingProvider(CreatingProvider):
    """A recorder whose provider contract explicitly supports due dates."""

    spec = ProviderSpec(
        name="rec-due",
        label="Due recorder",
        capabilities=Capabilities(
            move_issues=True,
            create_issues=True,
            writable_fields=("title", "body", "column_id", "labels", "due_date"),
        ),
    )


class AmbiguousCreatingProvider(CreatingProvider):
    """Creates remotely, then loses the response like a real network timeout."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        self.attempts += 1
        super().create_issue(project_id, draft)
        raise TransportError("response timed out after the create request")


class MalformedCreateResponseProvider(CreatingProvider):
    """Accept the POST, then fail while validating its response body."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        self.attempts += 1
        super().create_issue(project_id, draft)
        raise PayloadError("create response did not match the provider schema")


class DraftCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def project(self, provider: Provider) -> Project:
        return Project(
            provider=provider.spec.name,
            project_id=PROJECT.project_id,
            key=PROJECT.key,
            name=PROJECT.name,
        )

    def draft(self, provider: Provider, title: str, **kw: object) -> Path:
        """Write one draft the same way ``kbn new`` does."""
        return write_draft(
            self.ws,
            self.project(provider),
            TODO,
            IssueDraft(
                title=title,
                column_id=TODO.column_id,
                column_name=TODO.name,
                **kw,  # type: ignore[arg-type]
            ),
        )

    def sync(self, provider: Provider, **kw: object) -> SyncReport:
        options: dict[str, object] = {"commit": False, "confirm": lambda plan: True}
        options.update(kw)
        return sync(self.ws, provider, PROJECT, **options)  # type: ignore[arg-type]

    def drafts_on_disk(self) -> list[str]:
        return sorted(p.name for p in self.ws.rglob(f"{DRAFT_PREFIX}*.md"))


class DraftSurvivalTests(DraftCase):
    """A draft is not a deleted issue, however much it looks like one.

    The regression: ``_delete_vanished`` prunes any file whose id is missing
    from the fetch, and a draft's id is missing by definition. A dry run
    reported "deleted 3" and took three unsent stories with it.
    """

    def test_dry_run_leaves_drafts_alone(self) -> None:
        provider = CreatingProvider()
        for title in ("Port the picker", "Verify Linear", "Fix assignees"):
            self.draft(provider, title)

        # push_edits=False is what --dry-run passes.
        report = self.sync(provider, push_edits=False)

        self.assertEqual(3, len(self.drafts_on_disk()), "a dry run deleted the drafts")
        self.assertEqual([], report.deleted)
        self.assertEqual([], provider.created, "a dry run created issues on the tracker")

    def test_declined_sync_leaves_drafts_alone(self) -> None:
        provider = CreatingProvider()
        self.draft(provider, "Port the picker")

        report = self.sync(provider, confirm=lambda plan: False)

        self.assertEqual(1, len(self.drafts_on_disk()))
        self.assertEqual([], provider.created)
        self.assertTrue(report.declined)
        self.assertTrue(report.held, "a declined draft should be reported as held")

    def test_a_draft_survives_a_sync_that_pushes_other_things(self) -> None:
        """The prune runs on every sync, not only on dry runs."""
        existing = RemoteIssue(issue_id="id-1", key="JPT-1", title="Existing", column_id=TODO.column_id, status="To Do")
        provider = CreatingProvider([existing])
        self.sync(provider)  # lay the file down and snapshot it

        self.draft(provider, "Later thought")
        self.sync(provider, push_edits=False)

        self.assertEqual(["draft-later-thought.md"], self.drafts_on_disk())


class DraftCreationTests(DraftCase):
    """Confirming a sync turns the drafts into real issues.

    The regression: ``_create_drafts`` was written, tested by eye, and never
    called. The sync reported "no changes" with three drafts sitting in front
    of it.
    """

    def test_confirmed_sync_creates_them(self) -> None:
        provider = CreatingProvider()
        self.draft(provider, "Port the picker")
        self.draft(provider, "Verify Linear")

        report = self.sync(provider)

        self.assertEqual(
            ["Port the picker", "Verify Linear"],
            [draft.title for draft in provider.created],
        )
        self.assertEqual(2, len(report.created))

    def test_the_draft_file_becomes_its_real_key(self) -> None:
        provider = CreatingProvider()
        path = self.draft(provider, "Port the picker")

        self.sync(provider)

        self.assertFalse(path.exists(), "the draft file outlived its own creation")
        self.assertEqual([], self.drafts_on_disk())
        self.assertEqual(["JPT-101.md"], sorted(p.name for p in self.ws.rglob("JPT-*.md")))

    def test_discussion_survives_replacement_with_the_real_key(self) -> None:
        provider = CreatingProvider()
        path = self.draft(provider, "Port the picker")
        parsed = markdown.read(path)
        local_id = "comment-before-create"
        path.write_text(
            markdown.render(
                RemoteIssue(
                    issue_id=str(parsed.front["id"]),
                    title=str(parsed.front["title"]),
                    column_id=TODO.column_id,
                    status=TODO.name,
                    body=parsed.source,
                ),
                column_name=layout.column_folder(TODO),
                provider=provider.spec.name,
                comments=(
                    RemoteComment(
                        comment_id="imported-comment",
                        issue_id=str(parsed.front["id"]),
                        body="Imported provider discussion.",
                    ),
                ),
                comment_drafts=(
                    CommentDraft(
                        local_id=local_id,
                        issue_id=str(parsed.front["id"]),
                        body="Keep this pending comment **exactly**.",
                    ),
                ),
            ),
            encoding="utf-8",
        )

        self.sync(provider)

        canonical = markdown.read(next(self.ws.rglob("JPT-101.md")))
        self.assertEqual(["imported-comment"], [comment.comment_id for comment in canonical.comments])
        self.assertEqual("id-101", canonical.comments[0].issue_id)
        self.assertEqual("Imported provider discussion.", canonical.comments[0].body)
        self.assertEqual([local_id], [draft.local_id for draft in canonical.comment_drafts])
        self.assertEqual("id-101", canonical.comment_drafts[0].issue_id)
        self.assertEqual("Keep this pending comment **exactly**.", canonical.comment_drafts[0].body)

    def test_the_plan_lists_creates_before_anything_is_sent(self) -> None:
        provider = CreatingProvider()
        self.draft(
            provider,
            "Port the picker",
            body="Create the terminal picker.",
            issue_type="Story",
            labels=("tui",),
        )

        seen: list[SyncPlan] = []

        def confirm(plan: SyncPlan) -> bool:
            seen.append(plan)
            return False

        self.sync(provider, confirm=confirm)

        self.assertEqual(1, len(seen), "the plan should be offered exactly once")
        self.assertEqual(["Port the picker"], seen[0].creates)
        preview = seen[0].describe_sendable()
        self.assertIn("type: Story", preview)
        self.assertIn("column: To Do", preview)
        self.assertIn("fields: Summary, Description, Type, Status, Labels", preview)
        self.assertFalse(seen[0].is_empty())

    def test_one_confirmation_covers_creates_and_edits(self) -> None:
        """Two prompts in one sync teaches people to say yes without reading."""
        existing = RemoteIssue(issue_id="id-1", key="JPT-1", title="Existing", column_id=TODO.column_id, status="To Do")
        provider = CreatingProvider([existing])
        self.sync(provider)

        found = next(self.ws.rglob("JPT-1.md"))
        found.write_text(
            found.read_text(encoding="utf-8").replace("title: Existing", "title: Renamed"),
            encoding="utf-8",
        )
        self.draft(provider, "Port the picker")

        calls: list[SyncPlan] = []

        def accept(plan: SyncPlan) -> bool:
            calls.append(plan)
            return True

        self.sync(provider, confirm=accept)

        self.assertEqual(1, len(calls), "the sync asked more than once")
        self.assertEqual(["Port the picker"], calls[0].creates)
        self.assertEqual(1, len(calls[0].pushes))

    def test_body_and_labels_reach_the_tracker(self) -> None:
        provider = CreatingProvider()
        write_draft(
            self.ws,
            self.project(provider),
            TODO,
            IssueDraft(
                title="Port the picker",
                body="Lift it from pypanemux.",
                issue_type="Story",
                column_id=TODO.column_id,
                column_name=TODO.name,
                labels=("tui", "ux"),
            ),
        )

        self.sync(provider)

        made = provider.created[0]
        self.assertEqual("Story", made.issue_type)
        self.assertEqual(("tui", "ux"), made.labels)
        self.assertIn("Lift it from pypanemux.", made.body)

    def test_due_date_survives_markdown_translation_into_the_issue_draft(self) -> None:
        from datetime import date

        provider = DueCreatingProvider()
        self.draft(provider, "Ship the release", due_date=date(2026, 9, 17))

        self.sync(provider)

        self.assertEqual(date(2026, 9, 17), provider.created[0].due_date)

    def test_confirmation_does_not_promise_fields_the_provider_cannot_create(self) -> None:
        from datetime import date

        provider = CreatingProvider()
        self.draft(
            provider,
            "Port the picker",
            issue_type="Story",
            priority="High",
            due_date=date(2026, 9, 17),
        )
        seen: list[SyncPlan] = []

        def confirm(plan: SyncPlan) -> bool:
            seen.append(plan)
            return True

        self.sync(provider, confirm=confirm)

        preview = seen[0].describe_sendable()
        self.assertIn("type: Story", preview)
        self.assertIn("Type", preview)
        self.assertNotIn("Priority", preview)
        self.assertNotIn("Due Date", preview)

    def test_a_refused_create_is_reported_and_the_file_kept(self) -> None:
        provider = CreatingProvider()
        provider.refuse = True
        self.draft(provider, "Port the picker")

        report = self.sync(provider)

        self.assertEqual(1, len(report.skipped))
        self.assertIn("refused", report.skipped[0][1])
        self.assertEqual(1, len(self.drafts_on_disk()), "a refused create lost the draft")

    def test_a_tracker_that_cannot_create_says_so(self) -> None:
        provider = NoCreateProvider()
        self.draft(provider, "Port the picker")

        report = self.sync(provider)

        self.assertEqual(1, len(report.skipped))
        self.assertIn("cannot create", report.skipped[0][1])
        self.assertEqual(1, len(self.drafts_on_disk()))

    def test_creates_happen_in_the_order_they_were_written(self) -> None:
        """Five stories drafted in one go should land in that order."""
        provider = CreatingProvider()
        titles = [f"Story {n}" for n in range(1, 6)]
        for title in titles:
            self.draft(provider, title)

        self.sync(provider)

        self.assertEqual(titles, [draft.title for draft in provider.created])

    def test_an_ambiguous_create_is_not_retried_automatically(self) -> None:
        provider = AmbiguousCreatingProvider()
        draft = self.draft(provider, "Exactly once")

        first = self.sync(provider)
        second = self.sync(provider)

        self.assertEqual(1, provider.attempts)
        self.assertTrue(draft.exists())
        self.assertIn("outcome is unknown", " ".join(reason for _, reason in first.skipped))
        self.assertIn("not retried", " ".join(reason for _, reason in second.skipped))
        self.assertTrue(layout.pending_creates_file(self.ws).is_file())

    def test_a_malformed_success_response_is_ambiguous_and_never_retried(self) -> None:
        """A provider schema error happens after the POST may already exist remotely."""
        provider = MalformedCreateResponseProvider()
        draft = self.draft(provider, "Exactly once after malformed response")

        first = self.sync(provider)
        second = self.sync(provider)

        self.assertEqual(1, provider.attempts)
        self.assertTrue(draft.exists())
        self.assertIn("outcome is unknown", " ".join(reason for _, reason in first.skipped))
        self.assertIn("not retried", " ".join(reason for _, reason in second.skipped))

    def test_the_pending_create_journal_stores_only_a_content_fingerprint(self) -> None:
        provider = AmbiguousCreatingProvider()
        private_text = "customer incident details must not be copied into metadata"
        self.draft(provider, "Exactly once", body=private_text)

        self.sync(provider)

        raw = layout.pending_creates_file(self.ws).read_text(encoding="utf-8")
        self.assertNotIn(private_text, raw)
        from pykantui.workspace.pending import PendingCreateJournal

        pending = PendingCreateJournal.load(layout.pending_creates_file(self.ws)).attempts
        signature = next(iter(pending.values())).signature
        self.assertRegex(signature, r"\A[0-9a-f]{64}\Z")

    def test_an_explicit_retry_clears_the_ambiguous_create_journal_on_success(self) -> None:
        provider = CreatingProvider()
        draft = self.draft(provider, "Retry after checking")
        from pykantui.workspace.pending import PendingCreateJournal

        journal = PendingCreateJournal()
        journal.begin(
            layout.pending_creates_file(self.ws),
            draft.stem,
            filename=draft.name,
            signature="previous-attempt",
        )

        report = self.sync(provider, retry_ambiguous_creates=True)

        self.assertEqual(1, len(report.created))
        self.assertFalse(draft.exists())
        self.assertEqual({}, PendingCreateJournal.load(layout.pending_creates_file(self.ws)).attempts)

    def test_a_corrupt_pending_create_journal_blocks_all_creates(self) -> None:
        """Losing exactly-once state must fail closed instead of duplicating a card."""
        provider = CreatingProvider()
        draft = self.draft(provider, "Do not duplicate")
        journal = layout.pending_creates_file(self.ws)
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("{not valid json", encoding="utf-8")

        with self.assertRaisesRegex(ProviderError, "pending create journal"):
            self.sync(provider)

        self.assertEqual([], provider.created)
        self.assertTrue(draft.exists())


class DraftIdTests(unittest.TestCase):
    def test_is_draft_only_matches_the_prefix(self) -> None:
        self.assertTrue(is_draft("draft-port-the-picker"))
        self.assertFalse(is_draft("JPT-4"))
        self.assertFalse(is_draft("id-draft-4"))

    def test_a_draft_id_could_never_be_mistaken_for_a_key(self) -> None:
        """The id is visibly local, so a file read without the board is honest."""
        self.assertTrue(DRAFT_PREFIX.endswith("-"))
        self.assertTrue(is_draft(f"{DRAFT_PREFIX}anything"))


class DraftLayoutTests(DraftCase):
    def test_two_drafts_with_one_title_do_not_collide(self) -> None:
        provider = CreatingProvider()
        first = self.draft(provider, "Port the picker")
        second = self.draft(provider, "Port the picker")

        self.assertNotEqual(first, second)
        self.assertEqual(2, len(self.drafts_on_disk()))

    def test_a_draft_lands_in_its_column_folder(self) -> None:
        provider = CreatingProvider()
        path = self.draft(provider, "Port the picker")

        expected = layout.column_folder(TODO, layout.DEFAULT_COLUMN_STYLE)
        self.assertEqual(expected, path.parent.name)


def _cli_module() -> Any:
    """The ``pykantui.cli.main`` *module*.

    ``import pykantui.cli.main as cli`` binds by attribute lookup, and the
    package re-exports a ``main`` *function* under the same name -- so the
    obvious import hands back the function and patching it fails.
    """
    return importlib.import_module("pykantui.cli.main")


class ShowTests(DraftCase):
    """``kbn show`` is how a script or an agent reads a board.

    It used to always print the local JSON board, so standing in a workspace
    full of issues reported an empty board -- and it printed only the first
    line of each title, which for a workspace card is the bare issue key.

    The workspace backend is injected rather than discovered, because discovery
    needs a registered provider and what is under test is what ``_show`` does
    once it has one.
    """

    def show(self, provider: Provider) -> str:
        import contextlib
        import io
        import unittest.mock

        from pykantui.sync.provider import ProviderBackend

        cli = _cli_module()
        backend = ProviderBackend(self.ws, provider, PROJECT)
        buffer = io.StringIO()
        with (
            unittest.mock.patch.object(cli, "_workspace_backend", return_value=backend),
            contextlib.redirect_stdout(buffer),
        ):
            cli.main(["show"])
        return buffer.getvalue()

    def test_it_shows_the_key_and_the_title(self) -> None:
        existing = RemoteIssue(issue_id="id-1", key="JPT-1", title="Epic 1", column_id=TODO.column_id, status="To Do")
        provider = CreatingProvider([existing])
        self.sync(provider)

        text = self.show(provider)

        self.assertIn("JPT-1", text)
        self.assertIn("Epic 1", text, "the title was dropped, leaving a bare key")

    def test_drafts_appear_with_the_not_synced_marker(self) -> None:
        provider = CreatingProvider()
        self.draft(provider, "Port the picker")
        self.sync(provider, push_edits=False)

        text = self.show(provider)

        self.assertIn("Port the picker", text)
        self.assertIn("◌", text)

    def test_cards_are_numbered_by_position_in_the_column(self) -> None:
        provider = CreatingProvider()
        for title in ("First", "Second", "Third"):
            self.draft(provider, title)
        self.sync(provider, push_edits=False)

        numbers = [line.strip().split(".")[0] for line in self.show(provider).splitlines() if line.startswith("  ")]

        self.assertEqual(["1", "2", "3"], numbers, "every card claimed to be first")

    def test_it_looks_for_a_workspace_first(self) -> None:
        """The regression: ``show`` ignored the workspace it was standing in."""
        import contextlib
        import io
        import unittest.mock

        cli = _cli_module()
        with (
            unittest.mock.patch.object(cli, "_workspace_backend") as looked,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            looked.return_value = None  # no workspace: fall through to the JSON board
            cli.main(["show"])

        looked.assert_called_once()


class IssueTypeTests(unittest.TestCase):
    """Types come from the project, never from a list baked into the code.

    The regression this guards: the Jira create sent ``draft.issue_type or
    "Task"``. "Task" is not a type every project has, and Jira answers an
    unknown type with "the target project doesn't exist or you don't have
    permission to create issues in it" -- which sends you to check permissions
    for a problem that is nothing to do with them.
    """

    def setUp(self) -> None:
        self.provider = CreatingProvider()

    def test_the_default_is_an_ordinary_type_not_the_first_one(self) -> None:
        """Jira lists Epic before Task; defaulting to Epic creates a container."""
        default = self.provider.default_issue_type(PROJECT.project_id)

        self.assertIsNotNone(default)
        assert default is not None
        self.assertEqual("Task", default.name)
        self.assertEqual(0, default.level)

    def test_a_declared_default_wins_over_the_ordering(self) -> None:
        self.provider.types = [
            IssueType(type_id="3", name="Task", level=0),
            IssueType(type_id="4", name="Story", level=0, default=True),
        ]
        default = self.provider.default_issue_type(PROJECT.project_id)

        assert default is not None
        self.assertEqual("Story", default.name)

    def test_matching_ignores_case(self) -> None:
        found = self.provider.resolve_issue_type(PROJECT.project_id, "story")

        assert found is not None
        self.assertEqual("Story", found.name)

    def test_an_unknown_type_names_what_is_on_offer(self) -> None:
        with self.assertRaises(UnsupportedError) as caught:
            self.provider.resolve_issue_type(PROJECT.project_id, "Chore")

        message = str(caught.exception)
        self.assertIn("Chore", message)
        self.assertIn("Story", message, "the error should list the real choices")
        self.assertNotIn("Subtask", message, "a sub-task is not a choice for a new story")

    def test_a_tracker_with_no_types_accepts_what_it_is_given(self) -> None:
        """No list is "will not say", not "there are none" -- so do not refuse."""
        self.provider.types = []

        found = self.provider.resolve_issue_type(PROJECT.project_id, "Whatever")

        assert found is not None
        self.assertEqual("Whatever", found.name)
        self.assertIsNone(self.provider.default_issue_type(PROJECT.project_id))

    def test_the_list_is_fetched_once_per_project(self) -> None:
        for _ in range(4):
            self.provider.issue_types(PROJECT.project_id)
            self.provider.resolve_issue_type(PROJECT.project_id, "Story")

        self.assertEqual(1, self.provider.type_calls)

    def test_a_refresh_forgets_them(self) -> None:
        self.provider.issue_types(PROJECT.project_id)
        self.provider.refresh()
        self.provider.issue_types(PROJECT.project_id)

        self.assertEqual(2, self.provider.type_calls)

    def test_the_contract_default_is_no_types_rather_than_an_error(self) -> None:
        """A tracker without the concept must not have to implement anything.

        Built from a provider that overrides only the required methods -- so
        this fails if the default ever becomes a raise, which would make every
        typeless tracker implement a method to say "I have none".
        """

        class Bare(Provider):
            spec = ProviderSpec(
                name="bare",
                label="Bare",
                auth_fields=(ProviderField(name="t", label="T", kind=FieldKind.SECRET),),
            )

            def verify(self) -> RemoteUser:
                return RemoteUser(display_name="bare")

            def list_projects(self) -> list[RemoteProject]:
                return [PROJECT]

            def list_columns(self, project_id: str) -> list[RemoteColumn]:
                return COLUMNS

            def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
                return iter(())

        bare = Bare({}, {})
        self.assertEqual([], bare.issue_types(PROJECT.project_id))
        self.assertIsNone(bare.default_issue_type(PROJECT.project_id))


class StaleAfterCreate(CreatingProvider):
    """A tracker whose issue list lags its own creates -- i.e. a warm cache.

    ``refresh()`` drops the stale list, exactly as bypassing the cache does, so
    a sync that correctly refreshes sees the new issue and one that does not,
    does not.
    """

    def __init__(self, issues: list[RemoteIssue] | None = None) -> None:
        super().__init__(issues)
        self.frozen: list[RemoteIssue] | None = None

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(list(self.frozen if self.frozen is not None else self._issues))

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        if self.frozen is None:
            self.frozen = list(self._issues)  # what a cached list would still hold
        return super().create_issue(project_id, draft)

    def refresh(self) -> None:
        super().refresh()
        self.frozen = None


class RepullAfterCreateTests(DraftCase):
    """A create must bypass the cache on the way back, exactly as an edit does.

    The regression: the re-pull was refreshed only when ``sent_ids`` was
    non-empty, and ``sent_ids`` comes from the *edits*. A sync that only
    created issues therefore re-pulled through the cache, got a list from
    before they existed, and snapshotted that -- leaving a brand new issue
    showing as "not synced" on the board while sitting on the tracker.
    """

    def test_a_created_issue_is_snapshotted(self) -> None:
        provider = StaleAfterCreate()
        self.draft(provider, "Port the picker")

        report = self.sync(provider)

        self.assertEqual(["JPT-101"], report.created)
        state = SyncState.load(layout.state_file(self.ws))
        self.assertIsNotNone(
            state.get("id-101"),
            "the created issue has no snapshot, so the board calls it not-synced",
        )

    def test_it_does_not_show_as_never_synced(self) -> None:
        from pykantui.sync.provider import ProviderBackend
        from pykantui.workspace.status import SyncStatus

        provider = StaleAfterCreate()
        self.draft(provider, "Port the picker")
        self.sync(provider)

        backend = ProviderBackend(self.ws, provider, PROJECT)
        states = {task.title.splitlines()[0]: task.metadata.get("sync_status") for task in backend.get_tasks()}
        self.assertEqual(SyncStatus.SYNCED.value, states.get("JPT-101"))

    def test_an_edit_still_refreshes_too(self) -> None:
        """The original behaviour, kept."""
        existing = RemoteIssue(issue_id="id-1", key="JPT-1", title="Existing", column_id=TODO.column_id, status="To Do")
        provider = StaleAfterCreate([existing])
        self.sync(provider)

        found = next(self.ws.rglob("JPT-1.md"))
        found.write_text(
            found.read_text(encoding="utf-8").replace("title: Existing", "title: Renamed"),
            encoding="utf-8",
        )
        provider.frozen = list(provider._issues)  # go stale before the push

        self.sync(provider)

        self.assertIsNone(provider.frozen, "the push did not bypass the cache")


class LaggingIndexTests(DraftCase):
    """A search index that lags the tracker must not cost you files.

    Jira's JQL search is eventually consistent: an issue created a moment ago
    is routinely absent from the very next search, while a direct GET by key
    returns it perfectly. Measured -- JPT-14 was created, missed by the re-pull,
    and showed on the board as "never synced" while live on the tracker.
    """

    def test_a_created_issue_missing_from_the_repull_is_still_snapshotted(self) -> None:
        provider = StaleAfterCreate()
        provider.refresh = lambda: None  # type: ignore[method-assign]  # the index lags anyway
        self.draft(provider, "Port the picker")

        report = self.sync(provider)

        self.assertEqual(["JPT-101"], report.created)
        self.assertIsNotNone(
            SyncState.load(layout.state_file(self.ws)).get("id-101"),
            "an issue the index had not caught up with lost its snapshot",
        )

    def test_a_file_is_not_deleted_while_the_tracker_still_has_the_issue(self) -> None:
        """The dangerous half: prune on a stale list destroys a real file."""
        existing = RemoteIssue(issue_id="id-1", key="JPT-1", title="Existing", column_id=TODO.column_id, status="To Do")
        provider = CreatingProvider([existing])
        self.sync(provider)
        self.assertTrue(list(self.ws.rglob("JPT-1.md")))

        # The list goes stale -- as a lagging index does -- but get_issue,
        # which is served by id and not by the index, still answers.
        provider.iter_issues = lambda project_id: iter(())  # type: ignore[method-assign]

        report = self.sync(provider, push_edits=False)

        self.assertEqual([], report.deleted, "a lagging list deleted a live issue's file")
        self.assertTrue(list(self.ws.rglob("JPT-1.md")))

    def test_a_genuinely_deleted_issue_is_still_pruned(self) -> None:
        """The guard must not make deletion impossible."""
        existing = RemoteIssue(issue_id="id-1", key="JPT-1", title="Existing", column_id=TODO.column_id, status="To Do")
        provider = CreatingProvider([existing])
        self.sync(provider)

        provider._issues = []  # really gone: the list and get_issue both agree

        report = self.sync(provider, push_edits=False)

        self.assertEqual(["JPT-1.md"], report.deleted)
        self.assertEqual([], list(self.ws.rglob("JPT-1.md")))


class DraftStaysOnMyBoardTests(DraftCase):
    """A story you wrote must not vanish off your own board.

    The trap: a tracker sets the *reporter* to the caller and leaves the
    assignee empty. Under an assigned-only scope -- the default, because
    assigned is the work you actually have to do -- a story you drafted was
    therefore nobody's, and the very next sync archived it away.

    Survives the first sync either way, because the prune list is built before
    the create. It is the *second* sync that used to lose it, which is exactly
    the kind of bug that looks like it works.
    """

    def provider(self) -> CreatingProvider:
        class Reporting(CreatingProvider):
            def verify(self) -> RemoteUser:
                return RemoteUser(account_id="me-123", display_name="alex", email="j@x.com")

            def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
                made = super().create_issue(project_id, draft)
                # What a real tracker does: reporter is the caller.
                fixed = made.model_copy(update={"reporter_id": "me-123"})
                self._issues[-1] = fixed
                return fixed

        return Reporting()

    def mine_draft(self, provider: Provider, title: str) -> Path:
        """A draft the way ``kbn new`` writes one: assigned to you."""
        return write_draft(
            self.ws,
            self.project(provider),
            TODO,
            IssueDraft(
                title=title,
                column_id=TODO.column_id,
                column_name=TODO.name,
                assignee="alex",
                assignee_ids=("me-123",),
            ),
        )

    def test_it_survives_the_second_sync(self) -> None:
        from pykantui.tracker.mine import identify

        provider = self.provider()
        me = identify(provider.verify())
        self.mine_draft(provider, "Port the picker")

        self.sync(provider, identity=me)
        after_first = self.drafts_left()
        report = self.sync(provider, push_edits=False, identity=me)

        self.assertEqual([], report.archived, "the story I wrote was archived off my board")
        self.assertEqual(after_first, self.drafts_left())
        self.assertEqual(["JPT-101.md"], self.drafts_left())

    def test_the_create_asks_for_it_to_be_assigned_to_me(self) -> None:
        from pykantui.tracker.mine import identify

        provider = self.provider()
        me = identify(provider.verify())
        # write_draft records the assignee the way `kbn new` does
        write_draft(
            self.ws,
            self.project(provider),
            TODO,
            IssueDraft(
                title="Port the picker",
                column_id=TODO.column_id,
                column_name=TODO.name,
                assignee="alex",
                assignee_ids=("me-123",),
            ),
        )

        self.sync(provider, identity=me)

        self.assertEqual(("me-123",), provider.created[0].assignee_ids)

    def test_an_unassigned_draft_leaves_the_board_and_says_so(self) -> None:
        """Deliberate, not a bug: you said it is not yours to do.

        Archived rather than deleted, and reported, so a card leaving your
        board is something you are told about rather than something you
        notice missing later.
        """
        from pykantui.tracker.mine import identify

        provider = self.provider()
        me = identify(provider.verify())
        self.draft(provider, "Nobody's job")  # no assignee

        self.sync(provider, identity=me)
        report = self.sync(provider, push_edits=False, identity=me)

        self.assertEqual(["JPT-101.md"], report.archived)
        self.assertEqual([], report.deleted)

    def test_deleting_the_assignee_line_means_unassigned(self) -> None:
        """Removing it is how you say "not mine to do"."""
        from pykantui.tracker.mine import identify

        provider = self.provider()
        me = identify(provider.verify())
        path = write_draft(
            self.ws,
            self.project(provider),
            TODO,
            IssueDraft(
                title="Somebody else's job",
                column_id=TODO.column_id,
                column_name=TODO.name,
                assignee="alex",
                assignee_ids=("me-123",),
            ),
        )
        text = path.read_text(encoding="utf-8")
        kept = [line for line in text.splitlines() if not line.startswith("assignee:")]
        path.write_text("\n".join(kept), encoding="utf-8")

        self.sync(provider, identity=me)

        self.assertEqual((), provider.created[0].assignee_ids)

    def drafts_left(self) -> list[str]:
        from pykantui.workspace.sync import ARCHIVE_DIR

        return sorted(path.name for path in self.ws.rglob("JPT-*.md") if ARCHIVE_DIR not in path.parts)


if __name__ == "__main__":
    unittest.main()
