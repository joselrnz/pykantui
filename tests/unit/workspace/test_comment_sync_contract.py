"""Sync contract for pulled comments and confirmation-gated comment drafts."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from pykantui.api import PayloadError
from pykantui.config.paths import write_text_atomic
from pykantui.models import Task
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import NotFoundError, ProviderError, TransportError
from pykantui.tracker.models import (
    CommentDraft,
    IssueEdit,
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.spec import Capabilities, ProviderSpec
from pykantui.workspace import layout, markdown
from pykantui.workspace.comments import _finalized_draft_ids
from pykantui.workspace.models import PendingCommentPush, SyncPlan, SyncReport
from pykantui.workspace.outbound import CommentApplyResult
from pykantui.workspace.pending import PendingCommentJournal
from pykantui.workspace.sync import preview, sync

TODO = RemoteColumn(column_id="todo", name="To Do", position=0, group="todo")
PROJECT = RemoteProject(project_id="P1", key="JPT", name="Comment Project")
ISSUE = RemoteIssue(
    issue_id="10018",
    key="JPT-4",
    title="Commented card",
    column_id=TODO.column_id,
    status=TODO.name,
    body="Provider description",
)
LOCAL_ID = "comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA"


def draft(body: str = "Please verify this.") -> CommentDraft:
    return CommentDraft(
        local_id=LOCAL_ID,
        issue_id=ISSUE.issue_id,
        body=body,
        created_at=datetime(2026, 8, 13, 12, 45, tzinfo=UTC),
    )


def remote(comment_id: str, body: str) -> RemoteComment:
    return RemoteComment(
        comment_id=comment_id,
        issue_id=ISSUE.issue_id,
        body=body,
        author="José",
        author_id="acct-9",
        created_at=datetime(2026, 8, 13, 12, 30, tzinfo=UTC),
    )


class CommentProvider(Provider):
    """A fully provider-neutral fake with paged reads hidden by an iterator."""

    spec = ProviderSpec(
        name="comment-recorder",
        label="Comment Recorder",
        capabilities=Capabilities(
            writable_fields=("title",),
            read_comments=True,
            create_comments=True,
        ),
    )

    def __init__(self) -> None:
        super().__init__({}, {})
        self.remote_comments: list[RemoteComment] = []
        self.comment_attempts: list[CommentDraft] = []
        self.comment_reads = 0
        self.issue_reads = 0
        self.issue_updates: list[IssueEdit] = []
        self.ambiguous = False
        self.refuse = False
        self.fail_reads = False
        self.accepted_error: ProviderError | None = None
        self.blank_created_id = False

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me", display_name="Recorder")

    def list_projects(self) -> list[RemoteProject]:
        return [PROJECT]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return [TODO]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        self.issue_reads += 1
        yield ISSUE

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        return ISSUE if issue.issue_id == ISSUE.issue_id else None

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.issue_updates.append(edit)

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        self.comment_reads += 1
        if self.fail_reads:
            raise ProviderError("comment page unavailable")
        # Providers own pagination.  Returning an iterator here verifies that
        # workspace sync consumes every page/item without needing a list.
        yield from list(self.remote_comments)

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        comment: CommentDraft,
    ) -> RemoteComment:
        self.comment_attempts.append(comment)
        if self.refuse:
            raise ProviderError("comment refused")
        made = remote(f"remote-{len(self.remote_comments) + 1}", comment.body)
        self.remote_comments.append(made)
        if self.accepted_error is not None:
            raise self.accepted_error
        if self.ambiguous:
            raise TransportError("response lost after provider accepted the comment")
        if self.blank_created_id:
            return RemoteComment.model_construct(
                comment_id="   ",
                issue_id=issue.issue_id,
                body=comment.body,
            )
        return made


class ReadOnlyCommentProvider(CommentProvider):
    spec = ProviderSpec(
        name="comment-reader",
        label="Comment Reader",
        capabilities=Capabilities(read_comments=True, create_comments=False),
    )


class CommentPlanTests(unittest.TestCase):
    def test_remote_comment_id_cannot_be_blank(self) -> None:
        with self.assertRaises(ValueError):
            remote("   ", "unidentifiable")

    def test_comment_drafts_are_distinct_confirmable_plan_operations(self) -> None:
        item = PendingCommentPush(key="JPT-4", previous=ISSUE, draft=draft())

        plan = SyncPlan(comment_pushes=[item])

        self.assertFalse(plan.is_empty())
        self.assertEqual([], plan.pushes)
        self.assertIn("COMMENT (1)", plan.describe_sendable())
        self.assertIn("JPT-4", plan.describe_sendable())
        self.assertIn("Please verify this.", plan.describe_sendable())

    def test_comment_content_and_stable_id_participate_in_confirmation_identity(self) -> None:
        first = SyncPlan(
            comment_pushes=[PendingCommentPush(key="JPT-4", previous=ISSUE, draft=draft("one"))]
        )
        second = SyncPlan(
            comment_pushes=[PendingCommentPush(key="JPT-4", previous=ISSUE, draft=draft("two"))]
        )

        self.assertNotEqual(first.outbound_token(), second.outbound_token())

    def test_report_counts_posted_comments_separately_from_issue_edits(self) -> None:
        report = SyncReport(commented=["JPT-4"])

        self.assertEqual(1, report.total_changes())
        self.assertIn("commented 1", report.summary())

    def test_same_provider_comment_id_on_another_card_cannot_finalize_a_draft(self) -> None:
        applied = CommentApplyResult(
            journal=PendingCommentJournal(),
            journal_path=Path("pending-comments.json"),
            posted={"issue-b": [remote("1", "posted on B").model_copy(update={"issue_id": "issue-b"})]},
            confirmed_remote_ids={"local-a": "1"},
            draft_issue_ids={"local-a": "issue-a"},
        )

        self.assertEqual(set(), _finalized_draft_ids(applied, {"issue-a": ()}))
        self.assertEqual(
            {"local-a"},
            _finalized_draft_ids(
                applied,
                {"issue-a": (remote("1", "confirmed on A").model_copy(update={"issue_id": "issue-a"}),)},
            ),
        )


class CommentSyncCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_sync(
        self,
        provider: Provider,
        *,
        push_edits: bool = True,
        confirm: Callable[[SyncPlan], bool] | None = None,
        refresh_comments_for: set[str] | None = None,
    ) -> SyncReport:
        confirmation = confirm or (lambda plan: True)
        return sync(
            self.workspace,
            provider,
            PROJECT,
            commit=False,
            confirm=confirmation,
            push_edits=push_edits,
            refresh_comments_for=refresh_comments_for,
        )

    def card_path(self) -> Path:
        cards = list(self.workspace.rglob("JPT-4.md"))
        self.assertEqual(1, len(cards), f"expected one card, found {cards}")
        return cards[0]

    def write_drafts(self, comments: tuple[CommentDraft, ...]) -> None:
        path = self.card_path()
        parsed = markdown.read(path)
        write_text_atomic(
            path,
            markdown.render(
                ISSUE,
                column_name="to-do",
                provider="comment-recorder",
                notes=parsed.notes,
                comments=parsed.comments,
                comment_drafts=comments,
            ),
        )


class CommentPullTests(CommentSyncCase):
    def test_explicit_refresh_bypasses_the_selected_cache_before_read(self) -> None:
        class OrderedProvider(CommentProvider):
            def __init__(self) -> None:
                super().__init__()
                self.events: list[str] = []

            def iter_comments(
                self,
                project_id: str,
                issue: RemoteIssue,
            ) -> Iterator[RemoteComment]:
                self.events.append(f"read:{issue.issue_id}")
                yield from super().iter_comments(project_id, issue)

        provider = OrderedProvider()
        self.run_sync(provider, push_edits=False)

        self.run_sync(
            provider,
            push_edits=False,
            refresh_comments_for={ISSUE.issue_id},
        )

        self.assertEqual([f"read:{ISSUE.issue_id}"], provider.events)

    def test_sync_exhausts_the_provider_iterator_and_pulls_every_comment(self) -> None:
        provider = CommentProvider()
        provider.remote_comments = [remote(f"c-{index:03d}", f"reply {index}") for index in range(205)]

        self.run_sync(provider, push_edits=False)
        self.run_sync(
            provider,
            push_edits=False,
            refresh_comments_for={ISSUE.issue_id},
        )

        parsed = markdown.read(self.card_path())
        self.assertEqual(205, len(parsed.comments))
        self.assertEqual("c-000", parsed.comments[0].comment_id)
        self.assertEqual("c-204", parsed.comments[-1].comment_id)
        self.assertEqual(1, provider.comment_reads)

    def test_ordinary_sync_does_not_make_an_n_plus_one_comment_request(self) -> None:
        provider = CommentProvider()
        provider.remote_comments = [remote("c-1", "not requested yet")]

        self.run_sync(provider, push_edits=False)

        self.assertEqual(0, provider.comment_reads)
        parsed = markdown.read(self.card_path())
        self.assertFalse(parsed.has_comment_region)

    def test_opted_in_thread_uses_the_shared_comment_cache_between_syncs(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        provider.remote_comments = [remote("c-1", "cached reply")]

        self.run_sync(provider, push_edits=False)
        self.run_sync(provider, push_edits=False)

        self.assertEqual(1, provider.comment_reads)
        self.assertGreaterEqual(provider.cache.hits if provider.cache is not None else 0, 1)

    def test_explicit_refresh_fetches_only_the_requested_card(self) -> None:
        class ManyIssueProvider(CommentProvider):
            def __init__(self) -> None:
                super().__init__()
                self.issues = [
                    ISSUE.model_copy(update={"issue_id": str(index), "key": f"JPT-{index}"})
                    for index in range(1, 101)
                ]
                self.read_ids: list[str] = []

            def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
                yield from self.issues

            def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
                self.read_ids.append(issue.issue_id)
                return iter(())

        provider = ManyIssueProvider()
        self.run_sync(provider, push_edits=False)

        self.run_sync(provider, push_edits=False, refresh_comments_for={"42"})

        self.assertEqual(["42"], provider.read_ids)

    def test_explicit_refresh_skips_other_cards_with_cached_comment_regions(self) -> None:
        second_issue = ISSUE.model_copy(
            update={"issue_id": "10019", "key": "JPT-5", "title": "Another card"}
        )

        class TwoIssueProvider(CommentProvider):
            def __init__(self) -> None:
                super().__init__()
                self.issues = [ISSUE, second_issue]
                self.read_ids: list[str] = []

            def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
                yield from self.issues

            def iter_comments(
                self,
                project_id: str,
                issue: RemoteIssue,
            ) -> Iterator[RemoteComment]:
                self.read_ids.append(issue.issue_id)
                yield remote(f"remote-{issue.issue_id}", f"thread for {issue.issue_id}").model_copy(
                    update={"issue_id": issue.issue_id}
                )

        provider = TwoIssueProvider()
        self.run_sync(provider, push_edits=False)
        self.run_sync(
            provider,
            push_edits=False,
            refresh_comments_for={ISSUE.issue_id, second_issue.issue_id},
        )
        provider.read_ids.clear()

        self.run_sync(
            provider,
            push_edits=False,
            refresh_comments_for={ISSUE.issue_id},
        )

        self.assertEqual([ISSUE.issue_id], provider.read_ids)
        second_path = next(
            path
            for path in self.workspace.rglob("JPT-5.md")
            if path.name != layout.BOARD_FILE
        )
        self.assertEqual(
            [f"remote-{second_issue.issue_id}"],
            [item.comment_id for item in markdown.read(second_path).comments],
        )

    def test_a_failed_comment_refresh_preserves_the_last_complete_thread(self) -> None:
        provider = CommentProvider()
        provider.remote_comments = [remote("c-1", "last complete reply")]
        self.run_sync(provider, push_edits=False)
        self.run_sync(provider, push_edits=False, refresh_comments_for={ISSUE.issue_id})
        provider.remote_comments = [remote("c-2", "partial replacement must not land")]
        provider.fail_reads = True

        report = self.run_sync(provider, push_edits=False, refresh_comments_for={ISSUE.issue_id})

        parsed = markdown.read(self.card_path())
        self.assertEqual(["c-1"], [item.comment_id for item in parsed.comments])
        self.assertIn("comment page unavailable", " ".join(reason for _, reason in report.skipped))

    def test_pull_preserves_private_notes_and_unsent_drafts(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        path = self.card_path()
        parsed = markdown.read(path)
        write_text_atomic(
            path,
            markdown.render(
                ISSUE,
                column_name="to-do",
                provider=provider.spec.name,
                notes="never upload this",
                comments=parsed.comments,
                comment_drafts=parsed.comment_drafts,
            ),
        )
        provider.remote_comments = [remote("c-1", "provider reply")]

        self.run_sync(provider, push_edits=False)

        refreshed = markdown.read(path)
        self.assertEqual("never upload this", refreshed.notes)
        self.assertEqual(["provider reply"], [item.body for item in refreshed.comments])
        self.assertEqual([LOCAL_ID], [item.local_id for item in refreshed.comment_drafts])

    def test_local_edits_or_deletion_of_remote_comments_are_restored_not_sent(self) -> None:
        provider = CommentProvider()
        provider.remote_comments = [remote("c-1", "provider truth")]
        self.run_sync(provider, push_edits=False)
        self.run_sync(provider, push_edits=False, refresh_comments_for={ISSUE.issue_id})
        path = self.card_path()
        parsed = markdown.read(path)
        edited = parsed.comments[0].model_copy(update={"body": "local rewrite"})
        write_text_atomic(
            path,
            markdown.render(
                ISSUE,
                column_name="to-do",
                provider=provider.spec.name,
                notes=parsed.notes,
                comments=(edited,),
                comment_drafts=(),
            ),
        )

        self.run_sync(provider)

        restored = markdown.read(path)
        self.assertEqual(["provider truth"], [item.body for item in restored.comments])
        self.assertEqual([], provider.comment_attempts)
        self.assertEqual([], provider.issue_updates)

        write_text_atomic(
            path,
            markdown.render(
                ISSUE,
                column_name="to-do",
                provider=provider.spec.name,
                notes=restored.notes,
                comments=(),
                comment_drafts=(),
                include_comment_region=True,
            ),
        )
        self.run_sync(provider)

        restored_again = markdown.read(path)
        self.assertEqual(["provider truth"], [item.body for item in restored_again.comments])
        self.assertEqual([], provider.comment_attempts)

    def test_card_rewrite_with_comments_uses_the_atomic_writer(self) -> None:
        provider = CommentProvider()
        provider.remote_comments = [remote("c-1", "provider reply")]
        real_write = write_text_atomic
        markdown_targets: list[Path] = []

        def record(path: Path, text: str, *, private: bool = False) -> None:
            if path.suffix == ".md":
                markdown_targets.append(path)
            real_write(path, text, private=private)

        with patch("pykantui.workspace.disk.write_text_atomic", side_effect=record):
            self.run_sync(
                provider,
                push_edits=False,
                refresh_comments_for={ISSUE.issue_id},
            )

        self.assertIn(self.card_path(), markdown_targets)
        self.assertIn("pykantui:comments", self.card_path().read_text(encoding="utf-8"))


class CommentPushTests(CommentSyncCase):
    def test_a_confirmed_comment_posts_once_and_becomes_remote(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))

        first = self.run_sync(provider)
        second = self.run_sync(provider)

        self.assertEqual([LOCAL_ID], [item.local_id for item in provider.comment_attempts])
        self.assertEqual(["JPT-4"], first.commented)
        self.assertEqual([], second.commented)
        parsed = markdown.read(self.card_path())
        self.assertEqual((), parsed.comment_drafts)
        self.assertEqual(["Please verify this."], [item.body for item in parsed.comments])

    def test_declining_the_plan_keeps_the_draft_and_sends_nothing(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))

        report = self.run_sync(provider, confirm=lambda plan: False)

        self.assertTrue(report.declined)
        self.assertEqual([], provider.comment_attempts)
        self.assertEqual([LOCAL_ID], [item.local_id for item in markdown.read(self.card_path()).comment_drafts])

    def test_editing_an_unsent_draft_changes_the_single_post_body(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft("before"),))
        self.write_drafts((draft("after"),))

        self.run_sync(provider)

        self.assertEqual(["after"], [item.body for item in provider.comment_attempts])

    def test_deleting_an_unsent_draft_cancels_the_post(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        self.write_drafts(())

        self.run_sync(provider)

        self.assertEqual([], provider.comment_attempts)

    def test_a_comment_and_issue_edit_remain_two_distinct_plan_operations(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        path = self.card_path()
        write_text_atomic(
            path,
            path.read_text(encoding="utf-8").replace("title: Commented card", "title: Edited card"),
        )

        plan = preview(self.workspace, provider, PROJECT)

        self.assertEqual(1, len(plan.pushes))
        self.assertEqual(1, len(plan.comment_pushes))
        self.assertEqual(("title",), plan.pushes[0].edit.touched())

    def test_a_read_only_comment_provider_holds_the_draft(self) -> None:
        provider = ReadOnlyCommentProvider()
        self.run_sync(provider, push_edits=False)
        # The file is rendered for this provider, so keep its provider label.
        path = self.card_path()
        parsed = markdown.read(path)
        write_text_atomic(
            path,
            markdown.render(
                ISSUE,
                column_name="to-do",
                provider=provider.spec.name,
                notes=parsed.notes,
                comments=parsed.comments,
                comment_drafts=(draft(),),
            ),
        )

        report = self.run_sync(provider)

        self.assertEqual([], provider.comment_attempts)
        self.assertIn("cannot create comments", " ".join(reason for _, reason in report.skipped))
        self.assertEqual(1, len(markdown.read(path).comment_drafts))

    def test_duplicate_local_comment_id_across_cards_blocks_both_without_posting(self) -> None:
        second_issue = ISSUE.model_copy(
            update={"issue_id": "10019", "key": "JPT-5", "title": "Another card"}
        )

        class TwoIssueProvider(CommentProvider):
            def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
                yield ISSUE
                yield second_issue

            def get_issue(
                self,
                project_id: str,
                issue: RemoteIssue,
            ) -> RemoteIssue | None:
                return next(
                    (
                        candidate
                        for candidate in (ISSUE, second_issue)
                        if candidate.issue_id == issue.issue_id
                    ),
                    None,
                )

        provider = TwoIssueProvider()
        self.run_sync(provider, push_edits=False)
        paths = {
            markdown.read(path).front["id"]: path
            for path in self.workspace.rglob("*.md")
            if path.name != layout.BOARD_FILE
        }
        for issue in (ISSUE, second_issue):
            path = paths[issue.issue_id]
            parsed = markdown.read(path)
            write_text_atomic(
                path,
                markdown.render(
                    issue,
                    column_name="to-do",
                    provider=provider.spec.name,
                    notes=parsed.notes,
                    comments=parsed.comments,
                    comment_drafts=(draft().model_copy(update={"issue_id": issue.issue_id}),),
                ),
            )

        report = self.run_sync(provider)

        self.assertEqual([], provider.comment_attempts)
        self.assertIsNotNone(report.plan)
        assert report.plan is not None
        self.assertEqual(2, len(report.plan.invalid))
        self.assertTrue(
            all("duplicate comment draft id" in error for item in report.plan.invalid for error in item.errors)
        )
        for path in paths.values():
            self.assertEqual([LOCAL_ID], [item.local_id for item in markdown.read(path).comment_drafts])


class AmbiguousCommentTests(CommentSyncCase):
    def test_an_ambiguous_post_is_never_retried_automatically(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        provider.ambiguous = True

        first = self.run_sync(provider)
        second = self.run_sync(provider)

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertIn("outcome is unknown", " ".join(reason for _, reason in first.skipped))
        self.assertIn("not retried", " ".join(reason for _, reason in second.skipped))
        self.assertTrue(layout.pending_comments_file(self.workspace).is_file())
        self.assertEqual(1, len(markdown.read(self.card_path()).comment_drafts))

    def test_editing_a_draft_after_an_ambiguous_post_stays_held(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft("before"),))
        provider.ambiguous = True
        self.run_sync(provider)
        self.write_drafts((draft("after"),))

        report = self.run_sync(provider)

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertIn("draft changed afterward", " ".join(reason for _, reason in report.skipped))

    def test_a_definite_provider_rejection_does_not_poison_the_next_retry(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        provider.refuse = True

        self.run_sync(provider)
        provider.refuse = False
        self.run_sync(provider)

        self.assertEqual(2, len(provider.comment_attempts))
        self.assertFalse(layout.pending_comments_file(self.workspace).exists())

    def test_blank_created_comment_id_is_ambiguous_and_never_removes_the_draft(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        provider.blank_created_id = True

        first = self.run_sync(provider)
        second = self.run_sync(provider)

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertIn("outcome is unknown", " ".join(reason for _, reason in first.skipped))
        self.assertIn("not retried", " ".join(reason for _, reason in second.skipped))
        self.assertEqual([LOCAL_ID], [item.local_id for item in markdown.read(self.card_path()).comment_drafts])
        saved = PendingCommentJournal.load(layout.pending_comments_file(self.workspace))
        self.assertEqual("attempting", saved.attempts[LOCAL_ID].state)

    def test_payload_error_after_acceptance_is_ambiguous_and_not_replayed(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        provider.accepted_error = PayloadError("accepted response was malformed")

        first = self.run_sync(provider)
        second = self.run_sync(provider)

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertIn("outcome is unknown", " ".join(reason for _, reason in first.skipped))
        self.assertIn("not retried", " ".join(reason for _, reason in second.skipped))
        self.assertEqual([LOCAL_ID], [item.local_id for item in markdown.read(self.card_path()).comment_drafts])

    def test_not_found_readback_after_acceptance_is_ambiguous_and_not_replayed(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        provider.accepted_error = NotFoundError("accepted comment was missing on read-back")

        first = self.run_sync(provider)
        second = self.run_sync(provider)

        self.assertEqual(1, len(provider.comment_attempts))
        self.assertIn("outcome is unknown", " ".join(reason for _, reason in first.skipped))
        self.assertIn("not retried", " ".join(reason for _, reason in second.skipped))
        self.assertEqual([LOCAL_ID], [item.local_id for item in markdown.read(self.card_path()).comment_drafts])

    def test_success_followed_by_markdown_failure_never_reposts(self) -> None:
        provider = CommentProvider()
        self.run_sync(provider, push_edits=False)
        self.write_drafts((draft(),))
        card = self.card_path()
        real_write = write_text_atomic
        failed = False

        def fail_card_once(path: Path, text: str, *, private: bool = False) -> None:
            nonlocal failed
            if path == card and not failed:
                failed = True
                raise OSError("simulated atomic replace failure")
            real_write(path, text, private=private)

        with (
            patch("pykantui.workspace.disk.write_text_atomic", side_effect=fail_card_once),
            self.assertRaises(OSError),
        ):
            self.run_sync(provider)

        self.run_sync(provider)

        self.assertEqual(1, len(provider.comment_attempts), "a confirmed POST was duplicated after local failure")
        parsed = markdown.read(card)
        self.assertEqual((), parsed.comment_drafts)
        self.assertEqual(1, len(parsed.comments))


class ProviderBackendCommentTests(CommentSyncCase):
    """The TUI backend exposes cached discussions without provider leakage."""

    def make_backend(self, provider: CommentProvider) -> ProviderBackend:
        self.run_sync(provider, push_edits=False)
        return ProviderBackend(self.workspace, provider, PROJECT)

    def test_backend_reads_cached_comments_and_persists_a_draft_locally(self) -> None:
        provider = CommentProvider()
        backend = self.make_backend(provider)
        task = backend.get_tasks()[0]

        result = backend.save_comment_draft(task, "  Please check the migration.  ")

        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.task)
        assert result.task is not None
        self.assertEqual([], provider.comment_attempts)
        comments = backend.get_task_comments(result.task)
        self.assertEqual(1, len(comments))
        self.assertIsInstance(comments[0], CommentDraft)
        assert isinstance(comments[0], CommentDraft)
        self.assertEqual("Please check the migration.", comments[0].body)
        self.assertEqual(ISSUE.issue_id, comments[0].issue_id)
        self.assertRegex(comments[0].local_id, r"^comment-[0-9a-f]{32}$")
        self.assertIn("sync to send", result.message.lower())

    def test_backend_creates_distinct_stable_ids_for_separate_local_drafts(self) -> None:
        provider = CommentProvider()
        backend = self.make_backend(provider)
        first = backend.save_comment_draft(backend.get_tasks()[0], "First")
        self.assertTrue(first.ok, first.message)
        assert first.task is not None

        second = backend.save_comment_draft(first.task, "Second")

        self.assertTrue(second.ok, second.message)
        assert second.task is not None
        drafts = [
            item
            for item in backend.get_task_comments(second.task)
            if isinstance(item, CommentDraft)
        ]
        self.assertEqual(2, len(drafts))
        self.assertEqual(2, len({item.local_id for item in drafts}))

    def test_blank_draft_is_rejected_without_rewriting_the_card(self) -> None:
        provider = CommentProvider()
        backend = self.make_backend(provider)
        task = backend.get_tasks()[0]
        path = self.card_path()
        before = path.read_bytes()

        result = backend.save_comment_draft(task, " \n\t ")

        self.assertFalse(result.ok)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual((), markdown.read(path).comment_drafts)

    def test_explicit_backend_refresh_pulls_one_thread_without_sending(self) -> None:
        provider = CommentProvider()
        backend = self.make_backend(provider)
        task = backend.get_tasks()[0]
        provider.remote_comments = [remote("remote-1", "Provider reply")]
        provider.issue_reads = 0

        result = backend.refresh_task_comments(task)

        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.task)
        assert result.task is not None
        self.assertEqual(1, provider.comment_reads)
        self.assertEqual(0, provider.issue_reads, "a selected comment refresh must not refetch the whole board")
        self.assertEqual([], provider.comment_attempts)
        self.assertEqual([], provider.issue_updates)
        comments = backend.get_task_comments(result.task)
        self.assertEqual(["remote-1"], [item.comment_id for item in comments])

    def test_failed_backend_refresh_preserves_the_cached_thread_and_reports_failure(self) -> None:
        provider = CommentProvider()
        backend = self.make_backend(provider)
        provider.remote_comments = [remote("remote-1", "Last complete reply")]
        first = backend.refresh_task_comments(backend.get_tasks()[0])
        self.assertTrue(first.ok, first.message)
        assert first.task is not None
        provider.fail_reads = True

        failed = backend.refresh_task_comments(first.task)

        self.assertFalse(failed.ok)
        self.assertIn("comment page unavailable", failed.message)
        current = backend.get_tasks()[0]
        self.assertEqual(
            ["remote-1"],
            [item.comment_id for item in backend.get_task_comments(current)],
        )

    def test_stale_task_identity_cannot_refresh_or_save_the_wrong_row(self) -> None:
        provider = CommentProvider()
        backend = self.make_backend(provider)
        task = backend.get_tasks()[0]
        stale = task.model_copy(update={"metadata": {**task.metadata, "id": "different"}})

        refreshed = backend.refresh_task_comments(stale)
        saved = backend.save_comment_draft(stale, "Do not attach this")

        self.assertFalse(refreshed.ok)
        self.assertFalse(saved.ok)
        self.assertEqual(0, provider.comment_reads)
        self.assertEqual((), markdown.read(self.card_path()).comment_drafts)

    def test_comment_capabilities_are_card_scoped_and_require_a_local_file(self) -> None:
        provider = CommentProvider()
        backend = self.make_backend(provider)
        task = backend.get_tasks()[0]
        unrelated = Task(
            task_id=999,
            title="Elsewhere",
            column_id=1,
            metadata={"id": "elsewhere", "_source_revision": "x"},
        )

        self.assertTrue(backend.can_read_task_comments(task))
        self.assertTrue(backend.can_add_task_comment(task))
        self.assertFalse(backend.can_read_task_comments(unrelated))
        self.assertFalse(backend.can_add_task_comment(unrelated))


class PendingCommentJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "pending-comments.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_journal_is_content_free_and_written_atomically(self) -> None:
        journal = PendingCommentJournal()

        with patch("pykantui.workspace.pending.write_text_atomic", wraps=write_text_atomic) as atomic:
            journal.begin(
                self.path,
                LOCAL_ID,
                issue_id=ISSUE.issue_id,
                filename="JPT-4.md",
                signature="a" * 64,
            )

        atomic.assert_called_once()
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("Please verify this", raw)
        self.assertIn("\"signature\": \"" + ("a" * 64) + "\"", raw)

    def test_confirmed_remote_id_survives_until_the_markdown_is_finalized(self) -> None:
        journal = PendingCommentJournal()
        journal.begin(
            self.path,
            LOCAL_ID,
            issue_id=ISSUE.issue_id,
            filename="JPT-4.md",
            signature="a" * 64,
        )
        journal.confirm(self.path, LOCAL_ID, remote_id="remote-1")

        restored = PendingCommentJournal.load(self.path).attempts[LOCAL_ID]
        self.assertEqual("confirmed", restored.state)
        self.assertEqual("remote-1", restored.remote_id)

    def test_blank_remote_id_cannot_confirm_an_attempt(self) -> None:
        journal = PendingCommentJournal()
        journal.begin(
            self.path,
            LOCAL_ID,
            issue_id=ISSUE.issue_id,
            filename="JPT-4.md",
            signature="a" * 64,
        )

        with self.assertRaises(ValueError):
            journal.confirm(self.path, LOCAL_ID, remote_id="   ")

        restored = PendingCommentJournal.load(self.path).attempts[LOCAL_ID]
        self.assertEqual("attempting", restored.state)
        self.assertEqual("", restored.remote_id)

    def test_corrupt_journal_fails_closed(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertRaisesRegex(ProviderError, "pending comment journal"):
            PendingCommentJournal.load(self.path)


if __name__ == "__main__":
    unittest.main()
