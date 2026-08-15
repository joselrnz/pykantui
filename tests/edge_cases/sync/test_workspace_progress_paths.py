"""End-to-end workspace progress for every outbound disposition."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path

from pykantui.commands.new import write_draft
from pykantui.config.paths import write_text_atomic
from pykantui.tracker.models import IssueDraft, IssueEdit, RemoteColumn, RemoteIssue
from pykantui.workspace import markdown
from pykantui.workspace.models import ConfirmPush, SyncReport
from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate
from pykantui.workspace.project import Project
from pykantui.workspace.sync import sync
from tests.integration.sync.test_push import PROJECT, TODO, RecordingProvider, issue
from tests.unit.workspace.test_comment_sync_contract import (
    ISSUE as COMMENT_ISSUE,
)
from tests.unit.workspace.test_comment_sync_contract import (
    PROJECT as COMMENT_PROJECT,
)
from tests.unit.workspace.test_comment_sync_contract import (
    CommentProvider,
)
from tests.unit.workspace.test_comment_sync_contract import (
    draft as comment_draft,
)
from tests.unit.workspace.test_drafts import AmbiguousCreatingProvider, CreatingProvider


class AttemptProvider(RecordingProvider):
    """Record every requested update, including provider-refused attempts."""

    def __init__(self, issues: list[RemoteIssue]) -> None:
        super().__init__(issues)
        self.attempts: list[str] = []
        self.issue_list_calls = 0
        self.column_list_calls = 0

    def update_issue(self, remote: RemoteIssue, edit: IssueEdit) -> None:
        self.attempts.append(remote.display_key())
        super().update_issue(remote, edit)

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        self.issue_list_calls += 1
        yield from super().iter_issues(project_id)

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        self.column_list_calls += 1
        return super().list_columns(project_id)


class WorkspaceProgressPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def run_sync(
        self,
        provider: RecordingProvider,
        *,
        progress: Callable[[SyncProgressUpdate], None] | None = None,
        confirm: ConfirmPush | None = None,
        push_edits: bool = True,
    ) -> SyncReport:
        return sync(
            self.workspace,
            provider,
            PROJECT,
            commit=False,
            push_edits=push_edits,
            confirm=confirm or (lambda _plan: True),
            progress=progress,
        )

    def edit_title(self, key: str, title: str) -> None:
        path = next(self.workspace.rglob(f"{key}.md"))
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(f"title: Title {key}", f"title: {title}"), encoding="utf-8")

    def assert_terminal_once(self, seen: list[SyncProgressUpdate], phase: SyncPhase) -> None:
        terminal = [update for update in seen if not update.active]
        self.assertEqual(1, len(terminal), seen)
        self.assertEqual(phase, terminal[0].phase)

    def test_zero_card_pull_still_reports_each_ordered_workflow_phase(self) -> None:
        seen: list[SyncProgressUpdate] = []

        self.run_sync(AttemptProvider([]), push_edits=False, progress=seen.append)

        phases = list(dict.fromkeys(update.phase for update in seen))
        self.assertEqual(
            [
                SyncPhase.PREPARING,
                SyncPhase.FETCHING,
                SyncPhase.COMMENTS,
                SyncPhase.RECONCILING,
                SyncPhase.VERIFYING,
                SyncPhase.FINALIZING,
                SyncPhase.COMPLETE,
            ],
            phases,
        )
        self.assert_terminal_once(seen, SyncPhase.COMPLETE)
        self.assertEqual((0, 0), (seen[-1].completed, seen[-1].total))

    def test_one_card_pull_has_strict_phase_order_and_exact_terminal_fraction(self) -> None:
        seen: list[SyncProgressUpdate] = []

        self.run_sync(AttemptProvider([issue("K-1", TODO)]), push_edits=False, progress=seen.append)

        phases = list(dict.fromkeys(update.phase for update in seen))
        self.assertEqual(
            [
                SyncPhase.PREPARING,
                SyncPhase.FETCHING,
                SyncPhase.COMMENTS,
                SyncPhase.RECONCILING,
                SyncPhase.VERIFYING,
                SyncPhase.FINALIZING,
                SyncPhase.COMPLETE,
            ],
            phases,
        )
        self.assert_terminal_once(seen, SyncPhase.COMPLETE)
        self.assertEqual((1, 1, "K-1"), (seen[-1].completed, seen[-1].total, seen[-1].item))

    def test_declined_write_is_held_without_an_apply_or_provider_attempt(self) -> None:
        provider = AttemptProvider([issue("K-1", TODO)])
        self.run_sync(provider, push_edits=False)
        self.edit_title("K-1", "Local title")
        seen: list[SyncProgressUpdate] = []

        report = self.run_sync(provider, confirm=lambda _plan: False, progress=seen.append)

        self.assertTrue(report.declined)
        self.assertEqual([], provider.attempts)
        self.assertNotIn(SyncPhase.APPLYING, [update.phase for update in seen])
        self.assert_terminal_once(seen, SyncPhase.HELD)

    def test_conflict_is_counted_as_handled_but_never_written(self) -> None:
        provider = AttemptProvider([issue("K-1", TODO)])
        self.run_sync(provider, push_edits=False)
        self.edit_title("K-1", "Local title")
        provider._issues[0] = provider._issues[0].model_copy(update={"title": "Provider title"})
        seen: list[SyncProgressUpdate] = []

        report = self.run_sync(provider, progress=seen.append)

        self.assertTrue(report.skipped)
        self.assertEqual([], provider.attempts)
        applying = [update for update in seen if update.phase is SyncPhase.APPLYING]
        self.assertEqual([0, 1], [update.completed for update in applying])
        self.assert_terminal_once(seen, SyncPhase.HELD)

    def test_partial_failure_observer_errors_do_not_retry_or_duplicate_any_write(self) -> None:
        provider = AttemptProvider([issue(f"K-{number}", TODO) for number in range(1, 4)])
        self.run_sync(provider, push_edits=False)
        for number in range(1, 4):
            self.edit_title(f"K-{number}", f"Local {number}")
        provider.fail_on = {"K-2"}
        seen: list[SyncProgressUpdate] = []

        def broken_observer(update: SyncProgressUpdate) -> None:
            seen.append(update)
            if update.active and update.phase is SyncPhase.APPLYING:
                raise RuntimeError("dialog closed during callback")

        report = self.run_sync(provider, progress=broken_observer)

        self.assertEqual(["K-1", "K-2", "K-3"], provider.attempts)
        self.assertEqual(["K-1", "K-3"], [key for key, _edit in provider.updates])
        self.assertEqual(1, len(report.skipped))
        applying = [update for update in seen if update.phase is SyncPhase.APPLYING]
        self.assertEqual([0, 1, 1, 2, 2, 3], [update.completed for update in applying])
        self.assert_terminal_once(seen, SyncPhase.HELD)

    def test_progress_observation_never_adds_provider_reads(self) -> None:
        def request_counts(observe: bool) -> tuple[int, int]:
            with tempfile.TemporaryDirectory() as directory:
                provider = AttemptProvider([issue("K-1", TODO), issue("K-2", TODO)])
                sync(
                    Path(directory),
                    provider,
                    PROJECT,
                    push_edits=False,
                    commit=False,
                    progress=(lambda _update: None) if observe else None,
                )
                return provider.issue_list_calls, provider.column_list_calls

        self.assertEqual(request_counts(False), request_counts(True))


class WorkspaceCreateAndCommentProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def assert_terminal_once(self, seen: list[SyncProgressUpdate], phase: SyncPhase) -> None:
        terminal = [update for update in seen if not update.active]
        self.assertEqual(1, len(terminal), seen)
        self.assertEqual(phase, terminal[0].phase)

    def write_issue_draft(self, provider: CreatingProvider, title: str = "One local draft") -> None:
        project = Project(
            provider=provider.spec.name,
            project_id=PROJECT.project_id,
            key=PROJECT.key,
            name=PROJECT.name,
        )
        write_draft(
            self.workspace,
            project,
            TODO,
            IssueDraft(title=title, column_id=TODO.column_id, column_name=TODO.name),
        )

    def write_comment_draft(self, provider: CommentProvider) -> None:
        path = next(self.workspace.rglob("JPT-4.md"))
        parsed = markdown.read(path)
        write_text_atomic(
            path,
            markdown.render(
                COMMENT_ISSUE,
                column_name="to-do",
                provider=provider.spec.name,
                notes=parsed.notes,
                comments=parsed.comments,
                comment_drafts=(comment_draft(),),
            ),
        )

    def test_confirmed_create_is_attempted_once_and_accounted_for_once(self) -> None:
        provider = CreatingProvider()
        self.write_issue_draft(provider)
        seen: list[SyncProgressUpdate] = []

        report = sync(
            self.workspace,
            provider,
            PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=seen.append,
        )

        self.assertEqual(1, len(provider.created))
        self.assertEqual(["JPT-101"], report.created)
        applying = [update for update in seen if update.phase is SyncPhase.APPLYING]
        self.assertEqual([0, 1], [update.completed for update in applying])
        self.assert_terminal_once(seen, SyncPhase.COMPLETE)

    def test_refused_create_is_held_after_one_attempt_with_no_hidden_retry(self) -> None:
        provider = CreatingProvider()
        provider.refuse = True
        self.write_issue_draft(provider)
        attempts = 0
        original_create = provider.create_issue

        def count_create(project_id: str, draft: IssueDraft) -> RemoteIssue:
            nonlocal attempts
            attempts += 1
            return original_create(project_id, draft)

        provider.create_issue = count_create  # type: ignore[method-assign]
        seen: list[SyncProgressUpdate] = []

        report = sync(
            self.workspace,
            provider,
            PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=seen.append,
        )

        self.assertEqual(1, attempts)
        self.assertTrue(report.skipped)
        self.assertEqual(1, len(report.held))
        self.assertEqual(1, report.held.count(report.held[0]))
        self.assert_terminal_once(seen, SyncPhase.HELD)

    def test_ambiguous_create_is_not_replayed_by_a_later_sync(self) -> None:
        provider = AmbiguousCreatingProvider()
        self.write_issue_draft(provider)
        first_seen: list[SyncProgressUpdate] = []
        second_seen: list[SyncProgressUpdate] = []

        first = sync(
            self.workspace,
            provider,
            PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=first_seen.append,
        )
        second = sync(
            self.workspace,
            provider,
            PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=second_seen.append,
        )

        self.assertEqual(1, provider.attempts)
        self.assertEqual(1, len(first.held))
        self.assertEqual(1, len(second.held))
        self.assert_terminal_once(first_seen, SyncPhase.HELD)
        self.assert_terminal_once(second_seen, SyncPhase.HELD)

    def test_confirmed_comment_is_posted_once_and_accounted_for_once(self) -> None:
        provider = CommentProvider()
        sync(
            self.workspace,
            provider,
            COMMENT_PROJECT,
            push_edits=False,
            commit=False,
        )
        self.write_comment_draft(provider)
        seen: list[SyncProgressUpdate] = []

        report = sync(
            self.workspace,
            provider,
            COMMENT_PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=seen.append,
        )

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertEqual(["JPT-4"], report.commented)
        applying = [update for update in seen if update.phase is SyncPhase.APPLYING]
        self.assertEqual([0, 1], [update.completed for update in applying])
        self.assert_terminal_once(seen, SyncPhase.COMPLETE)

    def test_refused_comment_is_held_after_one_attempt_with_no_hidden_retry(self) -> None:
        provider = CommentProvider()
        provider.refuse = True
        sync(self.workspace, provider, COMMENT_PROJECT, push_edits=False, commit=False)
        self.write_comment_draft(provider)
        seen: list[SyncProgressUpdate] = []

        report = sync(
            self.workspace,
            provider,
            COMMENT_PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=seen.append,
        )

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertTrue(report.skipped)
        self.assertEqual(["JPT-4.md"], report.held)
        self.assert_terminal_once(seen, SyncPhase.HELD)

    def test_multiple_refused_comments_hold_their_shared_card_filename_only_once(self) -> None:
        provider = CommentProvider()
        provider.refuse = True
        sync(self.workspace, provider, COMMENT_PROJECT, push_edits=False, commit=False)
        path = next(self.workspace.rglob("JPT-4.md"))
        parsed = markdown.read(path)
        first = comment_draft()
        second = first.model_copy(update={"local_id": "comment-01J5KX7K9Z8F2N4Q6P3S1T0VWB"})
        write_text_atomic(
            path,
            markdown.render(
                COMMENT_ISSUE,
                column_name="to-do",
                provider=provider.spec.name,
                notes=parsed.notes,
                comments=parsed.comments,
                comment_drafts=(first, second),
            ),
        )
        seen: list[SyncProgressUpdate] = []

        report = sync(
            self.workspace,
            provider,
            COMMENT_PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=seen.append,
        )

        self.assertEqual(2, len(provider.comment_attempts))
        self.assertEqual(2, len(report.skipped))
        self.assertEqual(["JPT-4.md"], report.held)
        applying = [update for update in seen if update.phase is SyncPhase.APPLYING]
        self.assertEqual([0, 1, 1, 2], [update.completed for update in applying])
        self.assert_terminal_once(seen, SyncPhase.HELD)

    def test_ambiguous_comment_is_not_replayed_by_a_later_sync(self) -> None:
        provider = CommentProvider()
        provider.ambiguous = True
        sync(self.workspace, provider, COMMENT_PROJECT, push_edits=False, commit=False)
        self.write_comment_draft(provider)
        first_seen: list[SyncProgressUpdate] = []
        second_seen: list[SyncProgressUpdate] = []

        first = sync(
            self.workspace,
            provider,
            COMMENT_PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=first_seen.append,
        )
        second = sync(
            self.workspace,
            provider,
            COMMENT_PROJECT,
            commit=False,
            confirm=lambda _plan: True,
            progress=second_seen.append,
        )

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertEqual(["JPT-4.md"], first.held)
        self.assertEqual(["JPT-4.md"], second.held)
        self.assert_terminal_once(first_seen, SyncPhase.HELD)
        self.assert_terminal_once(second_seen, SyncPhase.HELD)


if __name__ == "__main__":
    unittest.main()
