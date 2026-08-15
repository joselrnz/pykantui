"""RED contracts for local-first provider comments in every card view.

The production Comments UI does not exist yet.  These journeys deliberately
describe it from the user's side before implementation: comments are read from
the backend's local snapshot, a new comment is saved locally, and only Sync may
cross the provider-write boundary.
"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from threading import Event

from rich.text import Text
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static, TabbedContent, TextArea

from pykantui.models import BoardLayout, MoveResult, Task
from pykantui.pages.detail import TaskDetailScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.models import CommentDraft, RemoteComment
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from tests.integration.tui.test_board_tui import workflow_backend

NORMAL_SIZE = (150, 40)
COMPACT_SIZE = (96, 18)
CommentItem = RemoteComment | CommentDraft


class CommentsBackend(JsonBackend):
    """In-memory backend implementing the intended neutral comment contract."""

    supports_sync = True

    def __init__(
        self,
        *,
        read_comments: bool = True,
        add_comments: bool = True,
        label: str = "Jira",
        comments: dict[int, list[CommentItem]] | None = None,
    ) -> None:
        super().__init__()
        source = workflow_backend()
        for task in source.get_tasks():
            self.create_task(task.model_copy(deep=True))
        self.read_comments = read_comments
        self.add_comments = add_comments
        self.label = label
        self.comments = comments or {}
        self.comment_reads = 0
        self.local_saves = 0
        self.provider_creates = 0
        self.comment_refreshes = 0
        self.comment_error: Exception | None = None
        self.comment_gate: Event | None = None
        self.comment_gate_entered: Event | None = None

    def display_kind(self) -> str:
        return self.label

    def can_read_task_comments(self, task: Task) -> bool:
        del task
        return self.read_comments

    def can_add_task_comment(self, task: Task) -> bool:
        del task
        return self.read_comments and self.add_comments

    def get_task_comments(self, task: Task) -> tuple[CommentItem, ...]:
        self.comment_reads += 1
        if self.comment_gate_entered is not None:
            self.comment_gate_entered.set()
        if self.comment_gate is not None and not self.comment_gate.wait(timeout=2):
            raise TimeoutError("comment fixture was not released")
        if self.comment_error is not None:
            raise self.comment_error
        return tuple(self.comments.get(task.task_id, ()))

    def save_comment_draft(self, task: Task, body: str) -> MoveResult:
        text = body.strip()
        if not text:
            return MoveResult.failure("a comment cannot be empty")
        self.local_saves += 1
        self.comments.setdefault(task.task_id, []).append(
            CommentDraft(
                local_id=f"local-{self.local_saves}",
                issue_id=str(task.metadata.get("id", "") or task.task_id),
                body=text,
                created_at=datetime(2026, 8, 13, 15, 0, tzinfo=UTC),
            )
        )
        return MoveResult.success(task.model_copy(deep=True), "Comment saved locally · sync to send")

    def create_comment(self, task: Task, body: str) -> None:
        """Forbidden direct provider boundary, present to catch accidental use."""
        del task, body
        self.provider_creates += 1

    def refresh_task_comments(self, task: Task) -> MoveResult:
        """Simulate one explicit provider-read refresh into the local cache."""
        self.comment_refreshes += 1
        if not any(comment.comment_id == "remote-refreshed" for comment in self.comments.get(task.task_id, ())):
            self.comments.setdefault(task.task_id, []).append(
                RemoteComment(
                    comment_id="remote-refreshed",
                    issue_id=str(task.metadata.get("id", "") or task.task_id),
                    author="Katherine Johnson",
                    created_at=datetime(2026, 8, 13, 16, 0, tzinfo=UTC),
                    body="Refreshed from the provider snapshot.",
                )
            )
        return MoveResult.success(task.model_copy(deep=True), "Comments refreshed")


def two_comments() -> dict[int, list[CommentItem]]:
    start = datetime(2026, 8, 13, 14, 15, tzinfo=UTC)
    return {
        1: [
            RemoteComment(
                comment_id="remote-1",
                issue_id="1",
                author="Ada Lovelace",
                created_at=start,
                body="First review note.",
            ),
            RemoteComment(
                comment_id="remote-2",
                issue_id="1",
                author="Grace Hopper",
                created_at=start + timedelta(minutes=45),
                body="Ship it.",
            ),
        ]
    }


async def settle(pilot: Pilot[None], *, workers: bool = True) -> None:
    """Let Textual finish layout and, normally, local comment workers."""
    await pilot.pause()
    if workers:
        await asyncio.wait_for(pilot.app.workers.wait_for_complete(), timeout=5)
        await pilot.pause()


def text_of(widget: Static) -> str:
    """Return the literal visible text without interpreting Rich markup."""
    rendered = widget.render()
    return rendered.plain if isinstance(rendered, Text) else str(rendered)


async def wait_for_count(
    pilot: Pilot[None],
    root: Widget,
    selector: str,
    expected: int,
    *,
    timeout: float = 2.0,
) -> None:
    """Wait for one popup-local worker without awaiting its modal parent."""
    deadline = asyncio.get_running_loop().time() + timeout
    while len(root.query(selector)) != expected and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
        await pilot.pause()
    actual = len(root.query(selector))
    if actual != expected:
        raise AssertionError(f"expected {expected} nodes matching {selector!r}, got {actual}")


@asynccontextmanager
async def open_popup_comments(
    layout: BoardLayout,
    *,
    backend: CommentsBackend | None = None,
    size: tuple[int, int] = NORMAL_SIZE,
) -> AsyncIterator[tuple[KanbanApp, Pilot[None]]]:
    """Open the real Rows/Kanban detail popup and activate Comments."""
    app = KanbanApp(backend or CommentsBackend(comments=two_comments()), confirm_moves=False)
    async with app.run_test(size=size) as pilot:
        await settle(pilot)
        app.set_board_layout(layout)
        await settle(pilot)
        await pilot.press("v")
        await pilot.pause()
        if not isinstance(app.screen, TaskDetailScreen):
            raise AssertionError(f"expected TaskDetailScreen, got {type(app.screen).__name__}")
        await pilot.press("4")
        await pilot.pause()
        await wait_for_count(pilot, app.screen, ".provider-comment", 2)
        yield app, pilot


class SplitCommentsTests(unittest.IsolatedAsyncioTestCase):
    """The Split sidebar is the editor; it must never open a second dialog."""

    async def test_split_renders_counted_author_time_body_thread_in_place(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            await settle(pilot)
            root_screen = app.screen
            stack_size = len(app.screen_stack)

            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            self.assertIs(root_screen, app.screen)
            self.assertEqual(stack_size, len(app.screen_stack))
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))
            self.assertEqual(
                "Comments (2) 4",
                str(view.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab").label),
            )
            entries = list(view.query("#work-item-comments-list .provider-comment"))
            self.assertEqual(2, len(entries))
            self.assertEqual("Ada Lovelace", text_of(entries[0].query_one(".comment-author", Static)))
            self.assertIn("2026-08-13", text_of(entries[0].query_one(".comment-time", Static)))
            self.assertEqual("First review note.", text_of(entries[0].query_one(".comment-body", Static)))

            view.action_focus_tab("info")
            view.action_focus_tab("comments")
            await settle(pilot)
            self.assertEqual(1, backend.comment_reads, "reopening a cached tab must not contact the backend again")

    async def test_split_adds_a_pending_comment_locally_without_provider_write(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)
            root_screen = app.screen
            stack_size = len(app.screen_stack)

            draft = view.query_one("#work-item-comment-draft", TextArea)
            add = view.query_one("#work-item-comment-add-local", Button)
            self.assertEqual("Add locally", str(add.label))
            draft.load_text("Please include the migration note.")
            await pilot.pause()
            self.assertFalse(add.disabled)
            await pilot.click("#work-item-comment-add-local")
            await settle(pilot)

            self.assertEqual(1, backend.local_saves)
            self.assertEqual(0, backend.provider_creates)
            self.assertEqual("", draft.text)
            pending = list(view.query("#work-item-comments-list .pending-comment"))
            self.assertEqual(1, len(pending))
            self.assertEqual(
                "Please include the migration note.",
                text_of(pending[0].query_one(".comment-body", Static)),
            )
            self.assertIn("Pending · sends on Sync", text_of(pending[0].query_one(".comment-state", Static)))
            self.assertEqual(
                "Comments (3) 4",
                str(view.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab").label),
            )
            self.assertIs(root_screen, app.screen)
            self.assertEqual(stack_size, len(app.screen_stack))

    async def test_explicit_refresh_updates_the_local_thread_without_sending_a_comment(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            refresh = view.query_one("#work-item-comment-refresh", Button)
            self.assertFalse(refresh.disabled)
            await pilot.click("#work-item-comment-refresh")
            await settle(pilot)

            self.assertEqual(1, backend.comment_refreshes)
            self.assertEqual(0, backend.provider_creates)
            self.assertEqual(3, len(view.query("#work-item-comments-list .provider-comment")))
            self.assertEqual(
                "Comments (3) 4",
                str(view.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab").label),
            )

    async def test_blank_comment_keeps_add_disabled_and_never_writes(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            draft = view.query_one("#work-item-comment-draft", TextArea)
            draft.load_text("  \n  ")
            await pilot.pause()
            self.assertTrue(view.query_one("#work-item-comment-add-local", Button).disabled)
            await pilot.click("#work-item-comment-add-local")
            await settle(pilot)

            self.assertEqual("  \n  ", draft.text)
            self.assertEqual(0, backend.local_saves)
            self.assertEqual(0, backend.provider_creates)
            self.assertEqual(0, len(view.query("#work-item-comments-list .pending-comment")))

    async def test_failed_local_comment_save_preserves_the_recoverable_draft(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)
            draft = view.query_one("#work-item-comment-draft", TextArea)
            draft.load_text("Keep this after disk failure")
            await pilot.pause()
            self.assertFalse(view.query_one("#work-item-comment-add-local", Button).disabled)

            original = backend.save_comment_draft

            def fail_save(task: Task, body: str) -> MoveResult:
                del task, body
                return MoveResult.failure("could not write the pending comment")

            backend.save_comment_draft = fail_save  # type: ignore[method-assign]
            await pilot.click("#work-item-comment-add-local")
            await settle(pilot)
            backend.save_comment_draft = original  # type: ignore[method-assign]

            self.assertEqual("Keep this after disk failure", draft.text)
            self.assertFalse(view.query_one("#work-item-comment-add-local", Button).disabled)
            self.assertEqual(0, backend.local_saves)
            self.assertEqual(0, backend.provider_creates)

    async def test_switching_rows_discards_only_the_unsaved_composer_text(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)
            view.query_one("#work-item-comment-draft", TextArea).load_text("Unsaved scratch text")

            table = view.query_one("#work-items-table", DataTable)
            table.move_cursor(row=1)
            await settle(pilot)
            self.assertEqual(
                "Comments (0) 4",
                str(view.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab").label),
            )
            table.move_cursor(row=0)
            await settle(pilot)

            self.assertEqual("", view.query_one("#work-item-comment-draft", TextArea).text)
            self.assertEqual(
                "Comments (2) 4",
                str(view.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab").label),
            )
            self.assertEqual(0, backend.local_saves)
            self.assertEqual(0, backend.provider_creates)

    async def test_pending_comment_survives_local_reload_and_reselect(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            view.query_one("#work-item-comment-draft", TextArea).load_text("Persist after reload")
            await pilot.click("#work-item-comment-add-local")
            await settle(pilot)
            await app.action_refresh_board()
            await settle(pilot)
            view.action_focus_tab("comments")
            await settle(pilot)

            bodies = [
                text_of(item)
                for item in view.query("#work-item-comments-list .comment-body").results(Static)
            ]
            self.assertIn("Persist after reload", bodies)
            self.assertEqual(1, backend.local_saves)
            self.assertEqual(0, backend.provider_creates)

    async def test_read_only_provider_keeps_thread_but_disables_composer(self) -> None:
        backend = CommentsBackend(add_comments=False, label="Monday.com", comments=two_comments())
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            self.assertEqual(2, len(view.query("#work-item-comments-list .provider-comment")))
            self.assertTrue(view.query_one("#work-item-comment-draft", TextArea).disabled)
            self.assertTrue(view.query_one("#work-item-comment-add-local", Button).disabled)
            self.assertIn(
                "Read-only · Monday.com cannot add comments",
                text_of(view.query_one("#work-item-comments-state", Static)),
            )

    async def test_unsupported_provider_does_not_attempt_a_comment_read(self) -> None:
        backend = CommentsBackend(read_comments=False, add_comments=False, label="Offline JSON")
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            self.assertEqual(0, backend.comment_reads)
            self.assertIn(
                "Comments are unavailable for Offline JSON",
                text_of(view.query_one("#work-item-comments-state", Static)),
            )
            self.assertTrue(view.query_one("#work-item-comment-draft", TextArea).disabled)
            self.assertTrue(view.query_one("#work-item-comment-add-local", Button).disabled)

    async def test_empty_thread_has_a_clear_state_and_enabled_composer(self) -> None:
        app = KanbanApp(CommentsBackend(), confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            self.assertIn("No comments yet", text_of(view.query_one("#work-item-comments-state", Static)))
            self.assertFalse(view.query_one("#work-item-comment-draft", TextArea).disabled)
            self.assertTrue(view.query_one("#work-item-comment-add-local", Button).disabled)
            self.assertEqual(
                "Comments (0) 4",
                str(view.query_one("#work-item-tabs", TabbedContent).get_tab("work-item-comments-tab").label),
            )

    async def test_loading_and_error_states_are_explicit_and_recoverable(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        backend.comment_gate = Event()
        backend.comment_gate_entered = Event()
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await pilot.pause()

            entered = await asyncio.to_thread(backend.comment_gate_entered.wait, 1)
            self.assertTrue(entered, "comments must load off the Textual event loop")
            self.assertIn("Loading comments", text_of(view.query_one("#work-item-comments-state", Static)))
            backend.comment_gate.set()
            await settle(pilot)
            self.assertEqual(2, len(view.query("#work-item-comments-list .provider-comment")))

    async def test_comment_load_error_is_visible_and_disables_composer(self) -> None:
        backend = CommentsBackend(comments=two_comments())
        backend.comment_error = OSError("cached comments are unreadable")
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            state = text_of(view.query_one("#work-item-comments-state", Static))
            self.assertIn("Could not load comments", state)
            self.assertIn("cached comments are unreadable", state)
            self.assertTrue(view.query_one("#work-item-comment-draft", TextArea).disabled)

    async def test_comment_body_is_literal_and_control_safe(self) -> None:
        unsafe = "<script>alert(1)</script> [bold]literal[/bold] \x1b[31mred\x00"
        backend = CommentsBackend(
            comments={
                1: [
                    RemoteComment(
                        comment_id="unsafe",
                        issue_id="1",
                        author="<Admin> [italic]not markup[/italic]",
                        created_at=datetime(2026, 8, 13, tzinfo=UTC),
                        body=unsafe,
                    )
                ]
            }
        )
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=NORMAL_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            entry = view.query_one("#work-item-comments-list .provider-comment")
            author = text_of(entry.query_one(".comment-author", Static))
            body = text_of(entry.query_one(".comment-body", Static))
            self.assertEqual("<Admin> [italic]not markup[/italic]", author)
            self.assertIn("<script>alert(1)</script>", body)
            self.assertIn("[bold]literal[/bold]", body)
            self.assertNotIn("\x1b", body)
            self.assertNotIn("\x00", body)

    async def test_short_split_thread_scrolls_without_hiding_composer(self) -> None:
        start = datetime(2026, 8, 13, tzinfo=UTC)
        comments: dict[int, list[CommentItem]] = {
            1: [
                RemoteComment(
                    comment_id=str(index),
                    issue_id="1",
                    author=f"Author {index}",
                    created_at=start + timedelta(minutes=index),
                    body=f"Body {index}",
                )
                for index in range(30)
            ]
        }
        app = KanbanApp(CommentsBackend(comments=comments), confirm_moves=False)

        async with app.run_test(size=COMPACT_SIZE) as pilot:
            await settle(pilot)
            app.set_board_layout(BoardLayout.SPLIT)
            view = app.query_one(WorkItemsView)
            view.action_focus_tab("comments")
            await settle(pilot)

            thread = view.query_one("#work-item-comments-list", VerticalScroll)
            draft = view.query_one("#work-item-comment-draft", TextArea)
            add = view.query_one("#work-item-comment-add-local", Button)
            self.assertGreater(thread.max_scroll_y, 0)
            self.assertEqual(0, thread.max_scroll_x)
            self.assertEqual(1, thread.styles.scrollbar_size_vertical)
            draft.focus()
            await pilot.pause()
            pane = view.query_one("#work-item-comments-pane")
            self.assertTrue(draft.has_focus)
            self.assertLessEqual(draft.content_region.bottom, pane.content_region.bottom)
            self.assertGreater(add.region.width, 0)
            self.assertLessEqual(add.region.bottom, pane.content_region.bottom)
            self.assertGreaterEqual(draft.region.y, thread.region.bottom)
            self.assertGreaterEqual(add.region.y, draft.region.bottom)


class PopupCommentsTests(unittest.IsolatedAsyncioTestCase):
    """Rows and Kanban use the same detail popup and the same comment pane."""

    async def test_rows_popup_has_a_counted_comments_tab_and_stays_open_after_add(self) -> None:
        async with open_popup_comments(BoardLayout.ROWS) as (app, pilot):
            screen = app.screen
            self.assertEqual(
                "detail-comments-tab",
                screen.query_one("#detail-tabs", TabbedContent).active,
            )
            self.assertEqual(
                "Comments (2) 4",
                str(screen.query_one("#detail-tabs", TabbedContent).get_tab("detail-comments-tab").label),
            )
            screen.query_one("#detail-comment-draft", TextArea).load_text("Rows popup draft")
            await pilot.pause()
            self.assertFalse(screen.query_one("#detail-comment-add-local", Button).disabled)
            await pilot.click("#detail-comment-add-local")
            await wait_for_count(pilot, screen, ".pending-comment", 1)

            self.assertIs(screen, app.screen)
            self.assertEqual(1, len(screen.query("#detail-comments-list .pending-comment")))
            backend = app.backend
            self.assertIsInstance(backend, CommentsBackend)
            assert isinstance(backend, CommentsBackend)
            self.assertEqual(1, backend.local_saves)
            self.assertEqual(0, backend.provider_creates)

    async def test_kanban_popup_uses_the_same_comments_component(self) -> None:
        async with open_popup_comments(BoardLayout.KANBAN) as (app, _pilot):
            screen = app.screen
            self.assertEqual(2, len(screen.query("#detail-comments-list .provider-comment")))
            self.assertEqual("Ada Lovelace", text_of(screen.query_one(".comment-author", Static)))
            self.assertTrue(screen.query_one("#detail-comment-add-local", Button).display)

    async def test_compact_popup_scrolls_thread_and_keeps_composer_and_actions_reachable(self) -> None:
        start = datetime(2026, 8, 13, tzinfo=UTC)
        comments: dict[int, list[CommentItem]] = {
            1: [
                RemoteComment(
                    comment_id=str(index),
                    issue_id="1",
                    author=f"Author {index}",
                    created_at=start,
                    body=f"Long comment body {index}",
                )
                for index in range(30)
            ]
        }
        backend = CommentsBackend(
            comments=comments
        )
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=COMPACT_SIZE) as pilot:
            await settle(pilot)
            await pilot.press("v")
            await pilot.pause()
            self.assertIsInstance(app.screen, TaskDetailScreen)
            await pilot.press("4")
            await pilot.pause()
            await wait_for_count(pilot, app.screen, ".provider-comment", 30)

            screen = app.screen
            thread = screen.query_one("#detail-comments-list", VerticalScroll)
            draft = screen.query_one("#detail-comment-draft", TextArea)
            add = screen.query_one("#detail-comment-add-local", Button)
            close = screen.query_one("#detail-close", Button)
            self.assertGreater(thread.max_scroll_y, 0)
            self.assertEqual(0, thread.max_scroll_x)
            self.assertEqual(1, thread.styles.scrollbar_size_vertical)
            draft.focus()
            await pilot.pause()
            self.assertTrue(draft.has_focus)
            self.assertLessEqual(draft.content_region.bottom, screen.content_region.bottom)
            self.assertLessEqual(add.region.bottom, screen.content_region.bottom)
            self.assertLessEqual(close.region.bottom, screen.content_region.bottom)
            self.assertGreaterEqual(draft.region.y, thread.region.bottom)
            self.assertGreaterEqual(add.region.y, draft.region.bottom)


if __name__ == "__main__":
    unittest.main()
