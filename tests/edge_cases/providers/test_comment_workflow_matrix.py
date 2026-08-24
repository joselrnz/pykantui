"""Twenty-plus-card comment workflows across every shipped provider contract.

These tests stay above adapter transports on purpose.  The adapter payload and
response shapes have their own contract suite; this matrix proves that the one
workspace/backend/UI workflow consuming those normalized values behaves the
same for all ten provider specs, without any network access.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from textual.widgets import Static, TabbedContent

from pykantui.config.paths import write_text_atomic
from pykantui.models import BoardLayout
from pykantui.pages.detail import TaskDetailScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError, TransportError
from pykantui.tracker.models import (
    ColumnGroup,
    CommentDraft,
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.registry import specs
from pykantui.tracker.spec import ProviderSpec
from pykantui.workspace import layout, markdown
from pykantui.workspace.pending import PendingCommentJournal
from pykantui.workspace.sync import preview, sync
from tests.integration.tui.test_comments_ui import CommentsBackend, settle, wait_for_count

CARD_COUNT = 27
THREAD_COUNT = 23
BASE_TIME = datetime(2026, 8, 14, 12, tzinfo=UTC)
COLUMN = RemoteColumn(column_id="todo", name="To Do", position=0, group=ColumnGroup.TODO)


class CommentMatrixProvider(Provider):
    """Network-free provider carrying a real shipped provider specification."""

    spec = specs()[0]

    def __init__(self, provider_spec: ProviderSpec) -> None:
        super().__init__({}, {})
        type(self).spec = provider_spec
        self.project = RemoteProject(
            project_id=f"{provider_spec.name}-comment-project",
            key=provider_spec.name.upper(),
            name=f"{provider_spec.label} comment matrix",
        )
        self.issues = [
            RemoteIssue(
                issue_id=f"{provider_spec.name}-{number:02d}",
                key=f"{provider_spec.name.upper()}-{number:02d}",
                title=f"Comment workflow {number:02d}",
                body=f"Provider body {number:02d}",
                column_id=COLUMN.column_id,
                status=COLUMN.name,
            )
            for number in range(1, CARD_COUNT + 1)
        ]
        self.remote_threads: dict[str, list[RemoteComment]] = {}
        self.comment_reads: list[str] = []
        self.comment_writes: list[CommentDraft] = []
        self.failure_by_issue: dict[str, str] = {}

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me", display_name="Comment Matrix")

    def list_projects(self) -> list[RemoteProject]:
        return [self.project]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        del project_id
        return [COLUMN]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        del project_id
        yield from self.issues

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        del project_id
        return next((item for item in self.issues if item.issue_id == issue.issue_id), None)

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        del project_id
        self.comment_reads.append(issue.issue_id)
        yield from self.remote_threads.get(issue.issue_id, ())

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        draft: CommentDraft,
    ) -> RemoteComment:
        del project_id
        self.comment_writes.append(draft)
        failure = self.failure_by_issue.get(issue.issue_id, "")
        if failure == "refused":
            raise ProviderError("comment refused by fixture")
        made = RemoteComment(
            comment_id=f"remote-{len(self.comment_writes):04d}",
            issue_id=issue.issue_id,
            body=draft.body,
            author="Provider User",
            created_at=BASE_TIME,
        )
        self.remote_threads.setdefault(issue.issue_id, []).append(made)
        if failure == "ambiguous":
            raise TransportError("response lost after the provider accepted the comment")
        return made


def thread_for(issue: RemoteIssue) -> list[RemoteComment]:
    return [
        RemoteComment(
            comment_id=f"{issue.issue_id}-comment-{number:02d}",
            issue_id=issue.issue_id,
            body=f"Reply {number:02d} with Markdown **bold** and Unicode 測試 ✓",
            author=f"Reviewer {number % 5}",
            created_at=BASE_TIME + timedelta(minutes=number),
        )
        for number in range(THREAD_COUNT)
    ]


class CommentWorkflowMatrixCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def workspace_for(self, provider: CommentMatrixProvider) -> Path:
        return self.root / provider.spec.name

    def pull_cards(self, provider: CommentMatrixProvider) -> Path:
        workspace = self.workspace_for(provider)
        sync(workspace, provider, provider.project, push_edits=False, commit=False)
        return workspace

    @staticmethod
    def card_paths(workspace: Path) -> list[Path]:
        return sorted(path for path in workspace.rglob("*.md") if path.name != layout.BOARD_FILE)

    @staticmethod
    def rewrite(
        path: Path,
        provider: CommentMatrixProvider,
        *,
        comments: tuple[RemoteComment, ...] = (),
        drafts: tuple[CommentDraft, ...] = (),
        notes: str = "private local note",
    ) -> None:
        parsed = markdown.read(path)
        issue_id = str(parsed.front["id"])
        issue = next(item for item in provider.issues if item.issue_id == issue_id)
        write_text_atomic(
            path,
            markdown.render(
                issue,
                column_name="to-do",
                provider=provider.spec.name,
                notes=notes,
                comments=comments,
                comment_drafts=drafts,
                include_comment_region=True,
            ),
        )


class TwentyPlusCommentPullTests(CommentWorkflowMatrixCase):
    def test_27_cards_are_lazy_then_21_opted_in_threads_pull_completely(self) -> None:
        for provider_spec in specs():
            with self.subTest(provider=provider_spec.name):
                provider = CommentMatrixProvider(provider_spec)
                for issue in provider.issues:
                    provider.remote_threads[issue.issue_id] = thread_for(issue)
                workspace = self.pull_cards(provider)

                self.assertEqual([], provider.comment_reads)
                paths = self.card_paths(workspace)
                self.assertEqual(CARD_COUNT, len(paths))
                for path in paths[:21]:
                    self.rewrite(path, provider)

                sync(workspace, provider, provider.project, push_edits=False, commit=False)

                self.assertEqual(21, len(provider.comment_reads))
                self.assertEqual(21, len(set(provider.comment_reads)))
                for path in paths[:21]:
                    parsed = markdown.read(path)
                    self.assertEqual(THREAD_COUNT, len(parsed.comments))
                    self.assertEqual("private local note", parsed.notes)
                for path in paths[21:]:
                    self.assertFalse(markdown.read(path).has_comment_region)

    def test_explicit_refresh_of_one_of_27_cards_is_one_read_and_survives_restart(self) -> None:
        for provider_spec in specs():
            with self.subTest(provider=provider_spec.name):
                provider = CommentMatrixProvider(provider_spec)
                target = provider.issues[20]
                provider.remote_threads[target.issue_id] = thread_for(target)
                workspace = self.pull_cards(provider)

                sync(
                    workspace,
                    provider,
                    provider.project,
                    push_edits=False,
                    commit=False,
                    refresh_comments_for={target.issue_id},
                )

                self.assertEqual([target.issue_id], provider.comment_reads)
                backend = ProviderBackend(workspace, provider, provider.project)
                task = next(item for item in backend.get_tasks() if item.metadata["id"] == target.issue_id)
                self.assertEqual(THREAD_COUNT, len(backend.get_task_comments(task)))
                self.assertEqual([target.issue_id], provider.comment_reads)


class TwentyPlusCommentPushTests(CommentWorkflowMatrixCase):
    def test_edit_delete_decline_confirm_and_restart_never_duplicate_27_drafts(self) -> None:
        for provider_spec in specs():
            with self.subTest(provider=provider_spec.name):
                provider = CommentMatrixProvider(provider_spec)
                workspace = self.pull_cards(provider)
                paths = self.card_paths(workspace)
                for number, path in enumerate(paths):
                    parsed = markdown.read(path)
                    issue_id = str(parsed.front["id"])
                    self.rewrite(
                        path,
                        provider,
                        drafts=(
                            CommentDraft(
                                local_id=f"draft-{provider_spec.name}-{number:02d}",
                                issue_id=issue_id,
                                body=f"Original comment {number:02d}",
                                created_at=BASE_TIME,
                            ),
                        ),
                    )

                plan = preview(workspace, provider, provider.project)
                self.assertEqual(CARD_COUNT, len(plan.comment_pushes))
                declined = sync(
                    workspace,
                    provider,
                    provider.project,
                    commit=False,
                    confirm=lambda _plan: False,
                )
                self.assertTrue(declined.declined)
                self.assertEqual([], provider.comment_writes)

                expected_bodies: list[str] = []
                for number, path in enumerate(paths):
                    parsed = markdown.read(path)
                    if number % 4 == 0:
                        drafts: tuple[CommentDraft, ...] = ()
                    elif number % 3 == 0:
                        edited = parsed.comment_drafts[0].model_copy(
                            update={"body": f"Edited before sync {number:02d}"}
                        )
                        drafts = (edited,)
                        expected_bodies.append(edited.body)
                    else:
                        drafts = parsed.comment_drafts
                        expected_bodies.append(drafts[0].body)
                    self.rewrite(
                        path,
                        provider,
                        comments=parsed.comments,
                        drafts=drafts,
                        notes=parsed.notes,
                    )

                confirmed = sync(
                    workspace,
                    provider,
                    provider.project,
                    commit=False,
                    confirm=lambda _plan: True,
                )
                sync(workspace, provider, provider.project, commit=False)

                self.assertEqual(expected_bodies, [item.body for item in provider.comment_writes])
                self.assertEqual(len(expected_bodies), len(confirmed.commented))
                self.assertEqual(
                    [],
                    [draft.local_id for path in paths for draft in markdown.read(path).comment_drafts],
                )
                restarted = ProviderBackend(workspace, provider, provider.project)
                all_comments = [
                    comment for task in restarted.get_tasks() for comment in restarted.get_task_comments(task)
                ]
                self.assertEqual(len(expected_bodies), len(all_comments))
                self.assertEqual(expected_bodies, [item.body for item in all_comments])

    def test_refused_and_ambiguous_posts_are_held_and_never_silently_replayed(self) -> None:
        for provider_spec in specs():
            with self.subTest(provider=provider_spec.name):
                provider = CommentMatrixProvider(provider_spec)
                workspace = self.pull_cards(provider)
                paths = self.card_paths(workspace)
                for number, path in enumerate(paths[:21]):
                    parsed = markdown.read(path)
                    issue_id = str(parsed.front["id"])
                    provider.failure_by_issue[issue_id] = (
                        "refused" if number % 3 == 1 else "ambiguous" if number % 3 == 2 else ""
                    )
                    self.rewrite(
                        path,
                        provider,
                        drafts=(
                            CommentDraft(
                                local_id=f"failure-{provider_spec.name}-{number:02d}",
                                issue_id=issue_id,
                                body=f"Failure matrix {number:02d}",
                                created_at=BASE_TIME,
                            ),
                        ),
                    )

                first = sync(workspace, provider, provider.project, commit=False)
                writes_after_first = list(provider.comment_writes)
                second = sync(workspace, provider, provider.project, commit=False)

                ambiguous_ids = {
                    issue_id for issue_id, failure in provider.failure_by_issue.items() if failure == "ambiguous"
                }
                first_ambiguous_writes = [item for item in writes_after_first if item.issue_id in ambiguous_ids]
                all_ambiguous_writes = [item for item in provider.comment_writes if item.issue_id in ambiguous_ids]
                self.assertEqual(first_ambiguous_writes, all_ambiguous_writes)
                self.assertEqual(7, len(first.commented))
                self.assertEqual(0, len(second.commented))
                self.assertEqual(14, len(set(first.held)))
                journal = PendingCommentJournal.load(layout.pending_comments_file(workspace))
                self.assertEqual(7, len(journal.attempts))
                self.assertTrue(all(item.state == "attempting" for item in journal.attempts.values()))


class TwentyPlusCommentViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_provider_specs_share_27_card_comment_ui_in_every_layout(self) -> None:
        from pykantui.tui.app import KanbanApp
        from pykantui.tui.widgets.work_items import WorkItemsView

        comments: dict[int, list[RemoteComment | CommentDraft]] = {
            1: [
                RemoteComment(
                    comment_id=f"ui-{number:02d}",
                    issue_id="ui-1",
                    body=f"Visible UI reply {number:02d} **Markdown** 測試",
                    author=f"Reviewer {number % 5}",
                    created_at=BASE_TIME + timedelta(minutes=number),
                )
                for number in range(THREAD_COUNT)
            ]
        }
        provider_specs = specs()
        self.assertEqual(10, len(provider_specs))
        self.assertTrue(all(spec.capabilities.read_comments for spec in provider_specs))
        self.assertTrue(all(spec.capabilities.create_comments for spec in provider_specs))

        backend = CommentsBackend(label="Shared provider UI", comments=comments)
        templates = backend.get_tasks()
        template = templates[0]
        for task in templates:
            backend.delete_task(task.task_id)
        for number in range(1, CARD_COUNT + 1):
            backend.create_task(
                template.model_copy(
                    update={
                        "task_id": number,
                        "title": f"Provider UI card {number:02d}",
                        "position": number - 1,
                        "metadata": {
                            **template.metadata,
                            "id": f"shared-{number:02d}",
                            "key": f"SHARED-{number:02d}",
                        },
                    },
                    deep=True,
                )
            )

        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=(120, 32)) as pilot:
            await settle(pilot)
            self.assertEqual(CARD_COUNT, len(backend.get_tasks()))

            app.set_board_layout(BoardLayout.SPLIT)
            await settle(pilot)
            split = app.query_one(WorkItemsView)
            split.action_focus_tab("comments")
            await settle(pilot)
            self.assertEqual(
                THREAD_COUNT,
                len(split.query("#work-item-comments-list .provider-comment")),
            )
            label = split.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab").label
            self.assertIn(f"Comments ({THREAD_COUNT})", str(label))

            for layout_kind in (BoardLayout.ROWS, BoardLayout.KANBAN):
                app.set_board_layout(layout_kind)
                await settle(pilot)
                await pilot.press("v")
                await pilot.pause()
                self.assertIsInstance(app.screen, TaskDetailScreen)
                await pilot.press("4")
                await pilot.pause()
                await wait_for_count(
                    pilot,
                    app.screen,
                    ".provider-comment",
                    THREAD_COUNT,
                )
                state = app.screen.query_one(".comments-state", Static)
                self.assertNotIn("unavailable", str(state.render()).lower())
                await pilot.press("escape")
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                await pilot.pause()

        self.assertEqual(0, backend.provider_creates)
        self.assertEqual(0, backend.local_saves)


if __name__ == "__main__":
    unittest.main()
