"""Provider-neutral, local-first comment threads for card detail views."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Static, TextArea

from pykantui.i18n import translate as _
from pykantui.models import Task, same_task_identity
from pykantui.sync.base import Backend
from pykantui.tracker.models import CommentDraft, RemoteComment

if TYPE_CHECKING:
    from textual.worker import Worker

CommentItem = RemoteComment | CommentDraft


class CommentEntry(Vertical):
    """One literal, selectable comment in a chronological thread."""

    def __init__(self, comment: CommentItem) -> None:
        state_class = "pending-comment" if comment.pending else "provider-comment"
        super().__init__(classes=f"comment-entry {state_class}")
        self.comment = comment

    def compose(self) -> ComposeResult:
        with Horizontal(classes="comment-header"):
            yield Static(
                _safe_text(self.comment.author) or _("Unknown author"),
                classes="comment-author",
                markup=False,
            )
            yield Static(
                _timestamp(self.comment.created_at),
                classes="comment-time",
                markup=False,
            )
        yield Static(
            _safe_text(self.comment.body) or "—",
            classes="comment-body",
            markup=False,
        )
        if self.comment.pending:
            yield Static(
                _("Pending · sends on Sync"),
                classes="comment-state",
                markup=False,
            )


class CommentsPane(Vertical):
    """A lazy cached thread plus a local-only append composer.

    The pane knows only the neutral :class:`Backend` contract.  It never owns
    provider clients and never calls ``Provider.create_comment``; Add locally
    emits a message for the application controller to persist as a draft.
    """

    class CountChanged(Message):
        """Tell a host to refresh its ``Comments (N)`` tab label."""

        def __init__(self, pane: CommentsPane, count: int) -> None:
            self.pane = pane
            self.count = count
            super().__init__()

    class SaveRequested(Message):
        """Ask the app to persist one append-only comment draft locally."""

        def __init__(self, pane: CommentsPane, task: Task, body: str) -> None:
            self.pane = pane
            self.task = task
            self.body = body
            super().__init__()

    class RefreshRequested(Message):
        """Ask the backend to refresh this card's cached discussion."""

        def __init__(self, pane: CommentsPane, task: Task) -> None:
            self.pane = pane
            self.task = task
            super().__init__()

    def __init__(
        self,
        prefix: str,
        *,
        backend: Backend | None = None,
        task: Task | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id or f"{prefix}-comments-pane", classes="comments-pane")
        self.prefix = prefix
        self.backend = backend
        self._card = task.model_copy(deep=True) if task is not None else None
        self._comments: tuple[CommentItem, ...] = ()
        self._loaded = False
        self._loading = False
        self._load_failed = False
        self._saving = False
        self._generation = 0
        self._worker: Worker[None] | None = None

    @property
    def selected_task(self) -> Task | None:
        """The immutable card snapshot currently represented by this pane."""
        return self._card.model_copy(deep=True) if self._card is not None else None

    @property
    def comment_count(self) -> int:
        """Visible provider comments plus local pending drafts."""
        return len(self._comments)

    def compose(self) -> ComposeResult:
        yield Static("", id=f"{self.prefix}-comments-state", classes="comments-state", markup=False)
        yield VerticalScroll(id=f"{self.prefix}-comments-list", classes="comments-list")
        draft = TextArea("", id=f"{self.prefix}-comment-draft", classes="comment-draft")
        draft.border_title = _("New comment · local until Sync")
        yield draft
        with Horizontal(classes="comment-actions"):
            yield Button(_("Refresh comments"), id=f"{self.prefix}-comment-refresh")
            yield Button(
                _("Add locally"),
                id=f"{self.prefix}-comment-add-local",
                variant="primary",
            )

    def on_mount(self) -> None:
        self._sync_compact_layout()
        self._set_state(_("Open Comments to load the cached thread"))
        self._sync_controls()

    def on_resize(self, _event: events.Resize) -> None:
        """Keep the composer useful without crowding short sidebars/dialogs."""
        self._sync_compact_layout()

    def _sync_compact_layout(self) -> None:
        self.set_class(self.size.height < 14, "compact-comments")

    def bind_backend(self, backend: Backend) -> None:
        """Attach the neutral backend after a Split view has mounted."""
        self.backend = backend
        self._sync_controls()

    def set_task(self, task: Task | None) -> None:
        """Select a card without eagerly loading its comment thread."""
        if task is not None and self._card is not None and same_task_identity(task, self._card):
            self._card = task.model_copy(deep=True)
            return
        self._generation += 1
        self._card = task.model_copy(deep=True) if task is not None else None
        self._comments = ()
        self._loaded = False
        self._loading = False
        self._load_failed = False
        self._saving = False
        self._draft().load_text("")
        self.post_message(self.CountChanged(self, 0))
        self._set_state(
            _("Select a card to view comments")
            if task is None
            else _("Open Comments to load the cached thread")
        )
        self._sync_controls()
        self.call_later(self._replace_entries, ())

    def represents(self, task: Task) -> bool:
        """Whether an asynchronous result still belongs in this pane."""
        return self._card is not None and same_task_identity(self._card, task)

    def activate(self, *, force: bool = False) -> None:
        """Load this selected card once when its Comments tab becomes active."""
        task = self.selected_task
        backend = self.backend
        if task is None or backend is None:
            self._set_state(_("Select a card to view comments"))
            self._sync_controls()
            return
        if not backend.can_read_task_comments(task):
            self._loaded = True
            self._comments = ()
            self._set_state(
                _("Comments are unavailable for {provider}").format(provider=backend.display_kind())
            )
            self.post_message(self.CountChanged(self, 0))
            self._sync_controls()
            self.call_later(self._replace_entries, ())
            return
        if self._loading:
            return
        if self._loaded and not force:
            self._show_loaded_state()
            self._sync_controls()
            return

        self._generation += 1
        generation = self._generation
        self._loading = True
        self._load_failed = False
        self._set_state(_("Loading comments…"))
        self._sync_controls()
        self._start(partial(self._load, task, generation))

    def save_started(self) -> None:
        """Lock the composer while its local atomic write is running."""
        self._saving = True
        self._sync_controls()

    def save_succeeded(self, task: Task) -> None:
        """Clear a persisted draft and reload its new pending entry."""
        if not self.represents(task):
            return
        self._saving = False
        self._draft().load_text("")
        self.activate(force=True)

    def save_failed(self, task: Task, message: str) -> None:
        """Keep recoverable composer text after a rejected local write."""
        if not self.represents(task):
            return
        self._saving = False
        self._set_state(_safe_text(message) or _("Could not save the comment locally"))
        self._sync_controls()

    def refresh_started(self, task: Task) -> None:
        """Show one stable loading state during explicit cache refresh."""
        if not self.represents(task):
            return
        self._loading = True
        self._set_state(_("Refreshing comments…"))
        self._sync_controls()

    def refresh_succeeded(self, task: Task) -> None:
        """Reload the snapshot after the backend refresh callback returns."""
        if not self.represents(task):
            return
        self._loading = False
        self.activate(force=True)

    def refresh_failed(self, task: Task, message: str) -> None:
        """Leave the existing thread visible when an explicit refresh fails."""
        if not self.represents(task):
            return
        self._loading = False
        self._set_state(
            _("Could not refresh comments · {error}").format(error=_safe_text(message))
        )
        self._sync_controls()

    @on(TextArea.Changed)
    def draft_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == f"{self.prefix}-comment-draft":
            self._sync_controls()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        task = self.selected_task
        if task is None:
            return
        if event.button.id == f"{self.prefix}-comment-add-local":
            event.stop()
            body = self._draft().text
            if not body.strip() or event.button.disabled:
                return
            self.save_started()
            self.post_message(self.SaveRequested(self, task, body))
        elif event.button.id == f"{self.prefix}-comment-refresh":
            event.stop()
            if event.button.disabled:
                return
            self.refresh_started(task)
            self.post_message(self.RefreshRequested(self, task))

    async def _load(self, task: Task, generation: int) -> None:
        backend = self.backend
        if backend is None:
            return
        try:
            comments = await asyncio.to_thread(backend.get_task_comments, task)
        except Exception as error:  # backend boundary supplies user-facing details
            if generation != self._generation or not self.represents(task):
                return
            self._comments = ()
            self._loaded = False
            self._loading = False
            self._load_failed = True
            await self._replace_entries(())
            self._set_state(
                _("Could not load comments · {error}").format(error=_safe_text(error))
            )
            self.post_message(self.CountChanged(self, 0))
            self._sync_controls()
            return

        if generation != self._generation or not self.represents(task):
            return
        self._comments = tuple(comments)
        self._loaded = True
        self._loading = False
        self._load_failed = False
        await self._replace_entries(self._comments)
        self._show_loaded_state()
        self.post_message(self.CountChanged(self, len(self._comments)))
        self._sync_controls()

    async def _replace_entries(self, comments: tuple[CommentItem, ...]) -> None:
        thread = self.query_one(f"#{self.prefix}-comments-list", VerticalScroll)
        await thread.remove_children()
        if comments:
            await thread.mount(*(CommentEntry(comment) for comment in comments))

    def _show_loaded_state(self) -> None:
        task = self.selected_task
        backend = self.backend
        if task is None or backend is None:
            self._set_state(_("Select a card to view comments"))
        elif not backend.can_add_task_comment(task):
            self._set_state(
                _("Read-only · {provider} cannot add comments").format(
                    provider=backend.display_kind()
                )
            )
        elif not self._comments:
            self._set_state(_("No comments yet"))
        else:
            self._set_state("")

    def _set_state(self, message: str) -> None:
        found = self.query(f"#{self.prefix}-comments-state")
        if not found:
            return
        state = found.first(Static)
        state.update(message)
        state.display = bool(message)

    def _sync_controls(self) -> None:
        if not self.is_mounted:
            return
        task = self.selected_task
        backend = self.backend
        can_read = bool(task and backend and backend.can_read_task_comments(task))
        can_add = bool(task and backend and backend.can_add_task_comment(task))
        unavailable = not can_add or self._loading or self._load_failed or self._saving
        draft = self._draft()
        draft.disabled = unavailable
        add = self.query_one(f"#{self.prefix}-comment-add-local", Button)
        add.disabled = unavailable or not draft.text.strip()
        refresh = self.query_one(f"#{self.prefix}-comment-refresh", Button)
        refresh.disabled = (
            not can_read
            or self._loading
            or self._saving
            or not callable(getattr(backend, "refresh_task_comments", None))
        )

    def _draft(self) -> TextArea:
        return self.query_one(f"#{self.prefix}-comment-draft", TextArea)

    def _start(self, callback: Callable[[], Coroutine[None, None, None]]) -> None:
        if not self.is_mounted:
            return
        self._worker = self.run_worker(
            callback,
            group=f"comments:{self.id}",
            exclusive=True,
            exit_on_error=False,
        )


def _safe_text(value: object) -> str:
    """Strip terminal controls while preserving ordinary Unicode and lines."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"} or (ord(character) >= 32 and ord(character) != 127)
    )


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return _("Time unavailable")
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value.tzinfo else value.strftime("%Y-%m-%d %H:%M")


__all__ = ["CommentEntry", "CommentsPane"]
