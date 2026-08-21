"""One :class:`~pykantui.sync.base.Backend` wrapping any tracker.

This is the bridge between the board and every provider. The TUI already
speaks ``Backend`` -- columns, tasks, ``move_task`` -- and knows nothing about
Jira or Plane; this class translates both into local Markdown changes. The
explicit Sync action is the one boundary that may write to a provider.

Two things worth knowing:

**It is backed by the workspace, not by the network.** Tasks are read from the
markdown files a sync produced, not fetched on demand. That keeps the board
instant, keeps it working offline, and means the UI and the files can never
disagree about what the board looks like.

**Edits are local first.** A TUI edit or move rewrites the Markdown and gains
an unsent marker. Sync previews and confirms the provider call later, so every
outbound change follows one visible safety path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from hashlib import blake2b
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from pykantui.config import BoardConfig
from pykantui.core.work_items import WorkItemColumn
from pykantui.models import BackendKind, Board, Column, MoveResult, Task
from pykantui.sync.base import Backend
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.filter_fields import FilterFieldSpec
from pykantui.tracker.models import (
    ColumnGroup,
    CommentDraft,
    IssueDraft,
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
)
from pykantui.workspace import layout, markdown
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.models import ConflictResolution
from pykantui.workspace.paths import ensure_workspace_path
from pykantui.workspace.progress import SyncProgressCallback
from pykantui.workspace.state import SyncState
from pykantui.workspace.status import SyncStatus, status_of
from pykantui.workspace.sync import SyncPlan, SyncReport


class PostSyncReloadError(ProviderError):
    """Provider reconciliation finished, but its local board could not reload."""

    def __init__(self, report: SyncReport, error: Exception) -> None:
        self.report = report
        self.reload_error = error
        detail = str(error).splitlines()[0] if str(error) else type(error).__name__
        super().__init__(
            f"provider sync completed, but the local board reload failed: {detail}",
            hint="Do not repeat provider writes; repair or reload the local workspace.",
        )


class ProviderBackend(Backend):
    """A tracker's project, presented to the board as an ordinary backend."""

    #: A coarse behavioural flag only. Most trackers are not Jira,
    #: so anything user-facing must go through :meth:`display_kind` instead --
    #: this enum has two values and cannot name them.
    kind: ClassVar[BackendKind] = BackendKind.JIRA

    #: Every tracker populates assignee, type and labels onto its cards, so
    #: the filter bar is useful for every tracker -- not only for Jira.
    supports_issue_fields: ClassVar[bool] = True

    #: Provider workspaces expose an explicit two-way Sync action.
    supports_sync: ClassVar[bool] = True

    #: **False**, and it does not mean the board is read-only.
    #:
    #: The finer ``can_*_tasks`` methods below gate provider workspaces. This
    #: broad historical flag stays false so older callers never infer that
    #: remote deletion is available merely because local editing is.
    #:
    #: Leaving it True offered "New card here" on the right-click menu and
    #: then failed when it was picked -- exactly the thing this codebase says
    #: not to do. Moving cards is gated by ``Capabilities.move_issues`` and
    #: text editing by the provider's declared writable fields.
    writable: ClassVar[bool] = False

    #: No client-side row order. Some trackers have one, but the board's order
    #: comes from the files, and reordering those is not a thing a tracker
    #: would hear about.
    supports_reorder: ClassVar[bool] = False

    def __init__(
        self,
        workspace: Path,
        provider: Provider,
        project: RemoteProject,
        *,
        column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
        show_team: bool = False,
    ) -> None:
        self.workspace = workspace
        self.provider = provider
        self.project = project
        self.column_style = column_style

        #: Show the rest of the project alongside your own cards.
        #:
        #: The three layers stay exactly as they are: the cache holds the whole
        #: project, the markdown holds only yours, and this decides what the
        #: board draws. Their cards come from the cached fetch, so turning it on
        #: costs no network and writes no files -- and they are read-only,
        #: because a card with no file behind it has nowhere to record an edit.
        self.show_team = show_team

        self._issues: dict[int, RemoteIssue] = {}
        self._paths: dict[int, Path] = {}
        self._source_revisions: dict[int, str] = {}
        self._columns: list[Column] = []
        self._warnings: list[str] = []
        self._theirs: set[int] = set()
        self._query_text = str(provider.config.get("jql", "") or "")
        self._query_results: tuple[RemoteIssue, ...] | None = None
        self.reload()

    # ---- loading ---------------------------------------------------------

    def reload(self) -> None:
        """Refresh the provider-backed shape, then re-read the local files."""
        self._warnings = []
        remote_columns = self.provider.columns(self.project.project_id)
        self._remote_columns = list(remote_columns)
        self._reload_visible_files()

    def reload_local(self) -> None:
        """Re-read Markdown and sync markers without contacting the provider."""
        self._warnings = []
        self._reload_visible_files()

    def _reload_visible_files(self) -> None:
        """Load local files, then reapply the current transient query overlay."""
        self._load_files(self._remote_columns, include_team=self._query_results is None)
        if self._query_results is not None:
            self._apply_query_overlay(self._query_results)

    def _load_files(self, remote_columns: list[RemoteColumn], *, include_team: bool = True) -> None:
        """Build the visible board from local Markdown and the last snapshot."""
        self._columns = [
            Column(column_id=index + 1, name=column.name, position=index) for index, column in enumerate(remote_columns)
        ]
        self._column_ids = {index + 1: column for index, column in enumerate(remote_columns)}
        self._by_remote = {column.column_id: index + 1 for index, column in enumerate(remote_columns)}

        state = SyncState.load(layout.state_file(self.workspace))
        folders = layout.folder_index(remote_columns, self.column_style)

        self._issues = {}
        self._paths = {}
        self._source_revisions = {}
        self._notes: dict[int, str] = {}
        self._agent_blocks: dict[int, str] = {}
        self._comments: dict[int, tuple[RemoteComment | CommentDraft, ...]] = {}
        self._status: dict[int, SyncStatus] = {}
        for number, path in enumerate(
            layout.iter_issue_files(self.workspace, self.provider.spec.name, self.project), start=1
        ):
            try:
                revision_before = _file_revision(path)
                parsed = markdown.read(path)
                revision_after = _file_revision(path)
            except OSError:
                continue
            if revision_before != revision_after:
                self._warnings.append(f"{path.name} changed while it was being read — reload to try again")
                continue
            issue_id = str(parsed.front.get("id", "") or "")
            snapshot = state.get(issue_id)
            if snapshot is None:
                # No snapshot means this file has never been synced. Skipping
                # it made the card *invisible* -- which is precisely the state
                # a locally created card starts in, so creating one would have
                # produced a file nobody could see. Show it from its own
                # frontmatter instead, marked as unsynced.
                snapshot = _issue_from_file(parsed, issue_id)
                self._warnings.append(f"{path.name} has not been synced yet")

            if not parsed.valid:
                details = "; ".join(parsed.errors)
                self._warnings.append(f"{path.name}: invalid Markdown — {details}")

            folder = layout.column_name_of(path, self.workspace, self.provider.spec.name, self.project)
            column = folders.get(folder)
            if column is not None:
                snapshot = snapshot.model_copy(update={"column_id": column.column_id})
            # The snapshot is the comparison baseline, not necessarily what
            # the person wants to see. Overlay the local file so Reload shows
            # an external editor's title/body immediately while the status
            # still says that version has not been sent.
            previous = state.get(issue_id)
            if previous is not None and parsed.valid:
                snapshot = _with_local_edits(
                    snapshot,
                    markdown.edit_from(
                        parsed,
                        column_id=column.column_id if column else previous.column_id,
                        previous=previous,
                    ),
                )
            self._issues[number] = snapshot
            self._paths[number] = path
            self._source_revisions[number] = revision_after
            self._notes[number] = parsed.notes
            self._agent_blocks[number] = parsed.agent_block
            self._comments[number] = (*parsed.comments, *parsed.comment_drafts)
            # Computed from local files only -- a card showing an unsent edit
            # must not cost a request, or the board stops being instant.
            self._status[number] = status_of(
                path,
                self.workspace,
                self.provider.spec.name,
                self.project,
                state,
                remote_columns,
                self.column_style,
            )

        self._theirs = set()
        if include_team and self.show_team:
            self._add_team(remote_columns)

    def _apply_query_overlay(self, results: tuple[RemoteIssue, ...]) -> None:
        """Show query matches over local files without changing those files.

        Matching local cards retain their Markdown edits and sync state.
        Recoverable local work stays visible even when it is not yet known to
        Jira. Matches without a local file are added read-only, exactly like
        team-context cards.
        """
        matched_ids = {issue.issue_id for issue in results if issue.issue_id}
        kept_numbers = {
            number
            for number, issue in self._issues.items()
            if issue.issue_id in matched_ids
            or self._status.get(number, SyncStatus.SYNCED).needs_attention()
        }
        known_ids = {issue.issue_id for issue in self._issues.values()}
        self._issues = {
            number: issue for number, issue in self._issues.items() if number in kept_numbers
        }
        self._paths = {number: path for number, path in self._paths.items() if number in kept_numbers}
        self._source_revisions = {
            number: revision
            for number, revision in self._source_revisions.items()
            if number in kept_numbers
        }
        self._notes = {number: notes for number, notes in self._notes.items() if number in kept_numbers}
        self._agent_blocks = {
            number: block for number, block in self._agent_blocks.items() if number in kept_numbers
        }
        self._comments = {
            number: comments for number, comments in self._comments.items() if number in kept_numbers
        }
        self._status = {
            number: status for number, status in self._status.items() if number in kept_numbers
        }
        self._theirs = set()

        number = max(self._issues, default=0)
        for issue in results:
            if not issue.issue_id or issue.issue_id in known_ids:
                continue
            number += 1
            self._issues[number] = issue
            self._notes[number] = ""
            self._agent_blocks[number] = ""
            self._comments[number] = ()
            self._status[number] = SyncStatus.SYNCED
            self._theirs.add(number)

    def _add_team(self, remote_columns: list[RemoteColumn]) -> None:
        """Add everyone else's cards, from the cache rather than the network.

        Served by the same issue list the last sync fetched, so opening the
        team view is instant and offline. A card already on disk is skipped:
        the file is the better copy, because it carries your notes and any
        unsent edit.
        """
        known = {issue.issue_id for issue in self._issues.values()}
        try:
            everything = list(self.provider.iter_issues(self.project.project_id))
        except ProviderError as error:
            self._warnings.append(f"could not load the rest of the board: {error}")
            return

        number = max(self._issues, default=0)
        for issue in everything:
            if issue.issue_id in known:
                continue
            number += 1
            self._issues[number] = issue
            self._notes[number] = ""
            self._agent_blocks[number] = ""
            self._comments[number] = ()
            self._status[number] = SyncStatus.SYNCED
            self._theirs.add(number)

    def owned_by_me(self, task_id: int) -> bool:
        """False for a card that belongs to somebody else and has no file."""
        return task_id not in self._theirs

    def display_kind(self) -> str:
        """The tracker's own name -- "Trello", not "jira"."""
        return self.provider.spec.label

    def warnings(self) -> list[str]:
        return list(self._warnings)

    def board_config(self) -> BoardConfig | None:
        """None: the columns come from the tracker, not from a local file.

        Returning a config here would offer the user a column editor whose
        changes the next sync would silently discard.
        """
        return None

    def can_create_tasks(self) -> bool:
        return self.provider.spec.capabilities.create_issues

    def can_edit_tasks(self) -> bool:
        return bool(self.provider.spec.capabilities.writable_fields)

    def can_edit_task(self, task: Task) -> bool:
        """Only locally mirrored cards may enter an editor."""
        return (
            self.can_edit_tasks()
            and self._issue_for_task(task) is not None
            and self._source_is_current(task)
            and self.owned_by_me(task.task_id)
        )

    def _issue_for_task(self, task: Task) -> RemoteIssue | None:
        """Resolve a UI task only when its immutable provider id still matches.

        ``task_id`` is a presentation row number rebuilt from the Markdown
        files on every reload.  It is therefore not safe as a write identity:
        deleting the first file can make the second provider issue inherit row
        number one while an older editor is still open.  The provider issue id
        stored in metadata is the stable identity and must agree before any
        local write is allowed.
        """
        issue = self._issues.get(task.task_id)
        if issue is None:
            return None
        provider_id = str(task.metadata.get("id", "") or "")
        return issue if provider_id == issue.issue_id else None

    def _source_is_current(self, task: Task) -> bool:
        """Refuse to overwrite a Markdown file changed after the UI loaded it."""
        expected = str(task.metadata.get("_source_revision", "") or "")
        path = self._paths.get(task.task_id)
        if not expected or path is None:
            return False
        try:
            return expected == _file_revision(path)
        except OSError:
            return False

    def can_delete_tasks(self) -> bool:
        return any(self.can_delete_task(task) for task in self.get_tasks())

    def can_delete_task(self, task: Task) -> bool:
        """Only a fresh, owned, valid draft that never reached the provider."""
        target, _message = self._draft_delete_target(task)
        return target is not None

    def delete_requires_confirmation(self, task: Task) -> bool:
        return self.can_delete_task(task)

    def editable_task_fields(self) -> frozenset[str]:
        return frozenset(self.provider.editable_card_fields())

    def creatable_task_fields(self) -> frozenset[str]:
        return frozenset(self.provider.creatable_card_fields())

    def supports_private_notes(self) -> bool:
        return True

    def can_read_task_comments(self, task: Task) -> bool:
        """Whether this locally mirrored card exposes provider discussion."""
        return (
            self.provider.spec.capabilities.read_comments
            and self._issue_for_task(task) is not None
            and self.owned_by_me(task.task_id)
        )

    def can_add_task_comment(self, task: Task) -> bool:
        """Whether a fresh local draft may be attached to this exact card."""
        return (
            self.can_read_task_comments(task)
            and self.provider.spec.capabilities.create_comments
            and self._source_is_current(task)
        )

    def get_task_comments(self, task: Task) -> tuple[RemoteComment | CommentDraft, ...]:
        """Return the cached provider thread followed by local pending drafts."""
        if self._issue_for_task(task) is None or not self.owned_by_me(task.task_id):
            return ()
        return self._comments.get(task.task_id, ())

    def save_comment_draft(self, task: Task, body: str) -> MoveResult:
        """Atomically append a local draft; explicit Sync performs the POST."""
        text = body.strip()
        if not text:
            return MoveResult.failure("a comment cannot be empty")
        issue = self._issue_for_task(task)
        if issue is None:
            return MoveResult.failure("that card changed while it was open — reload and try again")
        if not self.can_add_task_comment(task):
            return MoveResult.failure(f"{self.provider.spec.label} cannot add comments to that card")
        path = self._paths.get(task.task_id)
        if path is None:
            return MoveResult.failure("that card has no local Markdown file")

        from pykantui.config.paths import write_text_atomic  # noqa: PLC0415
        from pykantui.workspace.locking import exclusive_workspace  # noqa: PLC0415

        try:
            with exclusive_workspace(self.workspace):
                target = ensure_workspace_path(self.workspace, path)
                expected_revision = str(task.metadata.get("_source_revision", "") or "")
                if not expected_revision or _file_revision(target) != expected_revision:
                    return MoveResult.failure(
                        "that card's Markdown changed while it was open — reload and try again"
                    )
                parsed = markdown.read(target)
                if not parsed.valid:
                    return MoveResult.failure(
                        f"cannot add a comment to invalid Markdown — {'; '.join(parsed.errors)}"
                    )
                new_draft = CommentDraft(
                    local_id=f"comment-{uuid4().hex}",
                    issue_id=issue.issue_id,
                    body=text,
                )
                folder = layout.column_name_of(
                    target,
                    self.workspace,
                    self.provider.spec.name,
                    self.project,
                )
                write_text_atomic(
                    target,
                    markdown.render(
                        issue,
                        column_name=folder,
                        notes=parsed.notes,
                        provider=self.provider.spec.name,
                        agent_block=parsed.agent_block,
                        comments=parsed.comments,
                        comment_drafts=(*parsed.comment_drafts, new_draft),
                        include_comment_region=True,
                    ),
                )
        except (OSError, ProviderError) as error:
            return MoveResult.failure(str(error).splitlines()[0])

        self.reload_local()
        saved = self._task_for_issue_id(issue.issue_id)
        if saved is None:
            return MoveResult.failure("the comment draft was saved but the card could not be reloaded")
        return MoveResult.success(saved, "Comment saved locally · sync to send")

    def refresh_task_comments(self, task: Task) -> MoveResult:
        """Pull one card's provider discussion without sending pending work."""
        issue = self._issue_for_task(task)
        if issue is None:
            return MoveResult.failure("that card changed while it was open — reload and try again")
        if not self.can_read_task_comments(task):
            return MoveResult.failure(f"{self.provider.spec.label} cannot read comments for that card")
        if not self._source_is_current(task):
            return MoveResult.failure(
                "that card's Markdown changed while it was open — reload and try again"
            )

        try:
            from pykantui.config.paths import write_text_atomic  # noqa: PLC0415
            from pykantui.workspace.locking import exclusive_workspace  # noqa: PLC0415

            with exclusive_workspace(self.workspace):
                path = self._paths.get(task.task_id)
                if path is None:
                    return MoveResult.failure("that card has no local Markdown file")
                target = ensure_workspace_path(self.workspace, path)
                expected_revision = str(task.metadata.get("_source_revision", "") or "")
                if not expected_revision or _file_revision(target) != expected_revision:
                    return MoveResult.failure(
                        "that card's Markdown changed while it was open — reload and try again"
                    )
                parsed = markdown.read(target)
                if not parsed.valid:
                    return MoveResult.failure(
                        f"cannot refresh comments in invalid Markdown — {'; '.join(parsed.errors)}"
                    )
                comments = self.provider.comments(
                    self.project.project_id,
                    issue,
                    refresh=True,
                )
                folder = layout.column_name_of(
                    target,
                    self.workspace,
                    self.provider.spec.name,
                    self.project,
                )
                write_text_atomic(
                    target,
                    markdown.render(
                        issue,
                        column_name=folder,
                        notes=parsed.notes,
                        provider=self.provider.spec.name,
                        agent_block=parsed.agent_block,
                        comments=comments,
                        comment_drafts=parsed.comment_drafts,
                        include_comment_region=True,
                    ),
                )
        except (OSError, ProviderError) as error:
            return MoveResult.failure(str(error).splitlines()[0])
        self.reload_local()
        refreshed = self._task_for_issue_id(issue.issue_id)
        if refreshed is None:
            return MoveResult.failure("comments were refreshed but the card could not be reloaded")
        return MoveResult.success(refreshed, f"Comments refreshed from {self.provider.spec.label}")

    def _task_for_issue_id(self, issue_id: str) -> Task | None:
        """Resolve a card after a reload by its immutable provider identity."""
        return next(
            (task for task in self.get_tasks() if str(task.metadata.get("id", "") or "") == issue_id),
            None,
        )

    def provider_filter_fields(self) -> tuple[FilterFieldSpec, ...]:
        return self.provider.spec.filter_fields(self.provider.config)

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        """Provider-declared table fields for this exact project config."""
        return self.provider.spec.available_table_fields(self.provider.config)

    def invalidate(self) -> None:
        self.provider.forget_columns()
        self.reload()

    def plan_sync(self) -> SyncPlan:
        """Build an outbound plan without changing either side."""
        from pykantui.workspace.sync import preview  # noqa: PLC0415 - avoids a module cycle

        return preview(
            self.workspace,
            self.provider,
            self.project,
            column_style=self.column_style,
        )

    def sync_now(
        self,
        *,
        confirm: Callable[[SyncPlan], bool] | None,
        expected_plan: SyncPlan | None = None,
        commit: bool = True,
        push_edits: bool = True,
        push_conflicts: bool = False,
        accept_remote_conflicts: bool = False,
        conflict_resolutions: Mapping[str, Mapping[str, ConflictResolution]] | None = None,
        retry_ambiguous_creates: bool = False,
        retry_ambiguous_comments: bool = False,
        progress: SyncProgressCallback | None = None,
    ) -> SyncReport:
        """Run a two-way sync, then make the board reflect its files."""
        from pykantui.workspace.sync import sync  # noqa: PLC0415 - avoids a module cycle

        def confirm_current(plan: SyncPlan) -> bool:
            if expected_plan is not None and plan.outbound_token() != expected_plan.outbound_token():
                raise ProviderError(
                    "local changes changed since the confirmation was shown",
                    hint="Open Sync again and review the new send plan.",
                )
            return True if confirm is None else confirm(plan)

        report = sync(
            self.workspace,
            self.provider,
            self.project,
            confirm=confirm_current,
            commit=commit,
            push_edits=push_edits,
            push_conflicts=push_conflicts,
            accept_remote_conflicts=accept_remote_conflicts,
            conflict_resolutions=conflict_resolutions,
            retry_ambiguous_creates=retry_ambiguous_creates,
            retry_ambiguous_comments=retry_ambiguous_comments,
            known_conflicts=(
                {item.previous.issue_id for item in expected_plan.conflicts()} if expected_plan is not None else None
            ),
            column_style=self.column_style,
            progress=progress,
        )
        try:
            self.reload_local()
        except Exception as error:
            raise PostSyncReloadError(report, error) from error
        return report

    # ---- queries ---------------------------------------------------------

    def get_boards(self) -> list[Board]:
        return [self.get_active_board()]

    def get_active_board(self) -> Board:
        finish = next(
            (task_column for task_column, remote in self._column_ids.items() if remote.group == "done"),
            len(self._columns) or 1,
        )
        start = next(
            (task_column for task_column, remote in self._column_ids.items() if remote.group == "started"),
            1,
        )
        return Board(board_id=1, name=self.project.label(), start_column=start, finish_column=finish)

    def get_columns(self) -> list[Column]:
        return list(self._columns)

    def column_group(self, column_id: int) -> ColumnGroup:
        """Return the semantic group already normalised by the provider."""
        remote = self._column_ids.get(column_id)
        if remote is None:
            return ColumnGroup.UNKNOWN
        try:
            return ColumnGroup(remote.group)
        except (TypeError, ValueError):
            # Provider plugins may predate the validated RemoteColumn model.
            # An unknown value must not take down an otherwise readable board.
            return ColumnGroup.UNKNOWN

    def get_tasks(self) -> list[Task]:
        return [self._to_task(number, issue) for number, issue in sorted(self._issues.items())]

    def _to_task(self, number: int, issue: RemoteIssue) -> Task:
        return Task(
            task_id=number,
            title=f"{issue.display_key()}\n{issue.title}" if issue.key else issue.title,
            column_id=self._by_remote.get(issue.column_id, 1),
            description=issue.body,
            created_at=_naive(issue.created_at) or datetime.now(),
            started_at=_naive(issue.started_at),
            finished_at=_naive(issue.finished_at),
            due_date=issue.due_date,
            metadata={
                "key": issue.display_key(),
                "project": self.project.key or self.project.project_id,
                "id": issue.issue_id,
                "assignee": issue.assignee,
                "assignee_ids": list(issue.assignee_ids),
                "issue_type": issue.issue_type,
                "status": issue.status,
                "priority": issue.priority,
                "reporter": issue.reporter,
                "reporter_id": issue.reporter_id,
                "labels": list(issue.labels),
                "components": list(issue.components),
                "url": issue.url,
                "sync_status": self._status.get(number, SyncStatus.SYNCED).value,
                "private_notes": self._notes.get(number, ""),
                "_source_revision": self._source_revisions.get(number, ""),
                # Drawn differently and refused for edits: there is no file
                # behind it, so there is nowhere for a change to live.
                "mine": number not in self._theirs,
            },
        )

    # ---- writes ----------------------------------------------------------

    def move_task(self, task: Task, target_column: int, target_position: int | None = None) -> MoveResult:
        """Move the local Markdown; Sync is the only provider-write boundary."""
        issue_at_row = self._issues.get(task.task_id)
        issue = self._issue_for_task(task)
        column = self._column_ids.get(target_column)
        if issue_at_row is not None and issue is None:
            return MoveResult.failure("that card changed while it was open — reload and try again")
        if issue is None or column is None:
            return MoveResult.failure("that card is not on this board")
        if not self.owned_by_me(task.task_id):
            # Shown for context, not for editing. Moving it would change
            # somebody else's card on the tracker with no file on this machine
            # recording that it happened.
            return MoveResult.failure("that card is not yours — shown for context only")
        if not self._source_is_current(task):
            return MoveResult.failure("that card's Markdown changed while it was open — reload and try again")

        if not self.provider.spec.capabilities.move_issues:
            return MoveResult.failure(f"{self.provider.spec.label} cannot move issues from here")

        try:
            target = self._relocate_file(task.task_id, issue, column)
        except ProviderError as error:
            return MoveResult.failure(str(error).splitlines()[0])
        moved = issue.model_copy(update={"column_id": column.column_id, "status": column.name})
        self._issues[task.task_id] = moved
        self._paths[task.task_id] = target
        self._source_revisions[task.task_id] = _file_revision(target)
        self._status[task.task_id] = SyncStatus.EDITED

        metadata = dict(task.metadata)
        metadata["sync_status"] = SyncStatus.EDITED.value
        metadata["_source_revision"] = self._source_revisions[task.task_id]
        updated = task.model_copy(update={"column_id": target_column, "metadata": metadata})
        return MoveResult.success(updated, f"{issue.display_key()} → {column.name} · sync to send")

    def _relocate_file(self, number: int, issue: RemoteIssue, column: RemoteColumn) -> Path:
        """Put the markdown file in the folder its card now belongs to."""
        from pykantui import git  # noqa: PLC0415 - avoids a cycle at import time
        from pykantui.config.paths import write_text_atomic  # noqa: PLC0415

        name = self.provider.spec.name
        current = next(
            (
                path
                for path in layout.iter_issue_files(self.workspace, name, self.project)
                if str(markdown.read(path).front.get("id", "")) == issue.issue_id
            ),
            None,
        )
        target = ensure_workspace_path(
            self.workspace,
            layout.issue_path(self.workspace, name, self.project, column, issue, self.column_style),
        )
        if current is not None and current != target:
            source = ensure_workspace_path(self.workspace, current)
            if not git.move(self.workspace, source, target):
                raise ProviderError(
                    f"could not move {current.name} into {column.name}",
                    hint="The original Markdown file was left in place.",
                )

        moved = issue.model_copy(update={"column_id": column.column_id, "status": column.name})
        write_text_atomic(
            ensure_workspace_path(self.workspace, target),
            markdown.render(
                moved,
                column_name=layout.column_folder(column, self.column_style),
                notes=self._notes.get(number, ""),
                provider=name,
                agent_block=self._agent_blocks.get(number, ""),
            ),
        )
        return target

    def update_task(self, task: Task) -> MoveResult:
        """Save a TUI edit to Markdown; Sync sends it to the provider later."""
        issue_at_row = self._issues.get(task.task_id)
        issue = self._issue_for_task(task)
        column = self._column_ids.get(task.column_id)
        if issue_at_row is not None and issue is None:
            return MoveResult.failure("that card changed while it was open — reload and try again")
        if issue is None or column is None:
            return MoveResult.failure("that card is not on this board")
        if not self.owned_by_me(task.task_id):
            return MoveResult.failure("that card is not yours — shown for context only")
        if not self._source_is_current(task):
            return MoveResult.failure("that card's Markdown changed while it was open — reload and try again")

        allowed = self.editable_task_fields()
        title_lines = task.title.splitlines()
        summary = title_lines[-1].strip() if title_lines else ""
        if "title" in allowed and not summary:
            return MoveResult.failure("a card needs a title")
        if "column_id" not in allowed and column.column_id != issue.column_id:
            return MoveResult.failure(f"{self.provider.spec.label} cannot update card status")

        local = self._issue_with_allowed_edits(issue, task, column, allowed, summary)
        self._notes[task.task_id] = str(task.metadata.get("private_notes", "") or "")
        try:
            self._relocate_file(task.task_id, local, column)
        except ProviderError as error:
            return MoveResult.failure(str(error).splitlines()[0])
        self.reload_local()
        saved = next(
            (candidate for candidate in self.get_tasks() if candidate.metadata.get("id") == issue.issue_id),
            None,
        )
        if saved is None:
            return MoveResult.failure("the Markdown was saved but the card could not be reloaded")
        if saved.metadata.get("sync_status") == SyncStatus.EDITED.value:
            message = f"Saved locally · not sent to {self.provider.spec.label}"
        else:
            message = "Private Markdown notes saved locally"
        return MoveResult.success(saved, message)

    @staticmethod
    def _issue_with_allowed_edits(
        issue: RemoteIssue,
        task: Task,
        column: RemoteColumn,
        allowed: frozenset[str],
        summary: str,
    ) -> RemoteIssue:
        """Return a frozen provider issue containing only declared edits."""
        updates: dict[str, object] = {}
        if "title" in allowed:
            updates["title"] = summary
        if "body" in allowed:
            updates["body"] = task.description
        if "column_id" in allowed:
            updates.update(column_id=column.column_id, status=column.name)
        if "assignee" in allowed:
            updates["assignee"] = str(task.metadata.get("assignee", "") or "")
        if "labels" in allowed:
            labels = task.metadata.get("labels")
            if isinstance(labels, (list, tuple)):
                updates["labels"] = tuple(str(item) for item in labels)
        if "components" in allowed:
            components = task.metadata.get("components")
            if isinstance(components, (list, tuple)):
                updates["components"] = tuple(str(item) for item in components)
        if "due_date" in allowed:
            updates["due_date"] = task.due_date
        if "priority" in allowed:
            updates["priority"] = str(task.metadata.get("priority", "") or "")
        if "issue_type" in allowed:
            updates["issue_type"] = str(task.metadata.get("issue_type", "") or "")
        # RemoteIssue is a frozen Pydantic v2 model. The values above already
        # come from validated Task fields or are explicitly normalised, so a
        # model_copy is the correct cheap immutable update here.
        return issue.model_copy(update=updates)

    def create_task(self, task: Task) -> MoveResult:
        """Create a local draft; Sync performs the provider creation later."""
        if not self.can_create_tasks():
            return MoveResult.failure(f"{self.provider.spec.label} cards are created in its own UI")
        column = self._column_ids.get(task.column_id)
        if column is None:
            return MoveResult.failure("that column is not on this board")

        from pykantui.commands.new import write_draft  # noqa: PLC0415 - avoids command/provider cycle
        from pykantui.workspace.project import Project  # noqa: PLC0415

        labels = task.metadata.get("labels")
        components = task.metadata.get("components")
        draft = IssueDraft(
            title=task.title,
            body=task.description,
            column_id=column.column_id,
            column_name=column.name,
            priority=str(task.metadata.get("priority", "") or ""),
            labels=tuple(str(item) for item in labels) if isinstance(labels, list) else (),
            components=(
                tuple(str(item) for item in components)
                if isinstance(components, (list, tuple))
                else ()
            ),
            assignee=str(task.metadata.get("assignee", "") or ""),
            due_date=task.due_date,
        )
        record = Project(
            provider=self.provider.spec.name,
            project_id=self.project.project_id,
            key=self.project.key,
            name=self.project.name,
            owner=self.project.owner,
            column_style=self.column_style,
        )
        write_draft(self.workspace, record, column, draft)
        self.reload_local()
        created = next(
            (
                candidate
                for candidate in self.get_tasks()
                if candidate.title == task.title and candidate.metadata.get("sync_status") == SyncStatus.NEW.value
            ),
            None,
        )
        if created is None:
            return MoveResult.failure("the draft was written but could not be reloaded")
        return MoveResult.success(created, "Draft saved to Markdown · sync to create")

    def delete_task(self, task_id: int) -> MoveResult:
        task = next((item for item in self.get_tasks() if item.task_id == task_id), None)
        if task is None:
            return MoveResult.failure("that card changed while it was open — reload and try again")
        return self.delete_task_if_current(task)

    def delete_task_if_current(self, task: Task) -> MoveResult:
        """Quarantine an unsent draft after identity, revision, and path checks."""
        from pykantui.workspace.locking import exclusive_workspace  # noqa: PLC0415

        try:
            with exclusive_workspace(self.workspace):
                target, message = self._draft_delete_target(task)
                if target is None:
                    return MoveResult.failure(message)
                target = ensure_workspace_path(self.workspace, target)
                if not self._source_is_current(task):
                    return MoveResult.failure(
                        "that draft's Markdown changed during validation — reload and try again"
                    )
                destination_dir = ensure_workspace_path(
                    self.workspace,
                    (layout.trash_dir(self.workspace) / self.provider.spec.name).joinpath(
                        *self.project.path_parts()
                    ),
                )
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination = ensure_workspace_path(
                    self.workspace,
                    destination_dir / f"{target.stem}-{uuid4().hex}{target.suffix}",
                )
                target.replace(destination)
        except (OSError, ProviderError) as error:
            return MoveResult.failure(str(error).splitlines()[0])

        self.reload_local()
        relative = destination.relative_to(self.workspace).as_posix()
        return MoveResult.success(task, f"Draft moved to {relative} · provider not contacted")

    def _draft_delete_target(self, task: Task) -> tuple[Path | None, str]:
        """Resolve the exact deletable file without trusting a presentation row id."""
        from pykantui.commands.new import is_draft  # noqa: PLC0415

        issue = self._issue_for_task(task)
        if issue is None:
            return None, "that card changed while it was open — reload and try again"
        if (
            not self.owned_by_me(task.task_id)
            or self._status.get(task.task_id) is not SyncStatus.NEW
            or not is_draft(issue.issue_id)
        ):
            return None, "Only unsynced local drafts can be deleted here"
        path = self._paths.get(task.task_id)
        if path is None:
            return None, "that draft has no local Markdown file"
        try:
            target = ensure_workspace_path(self.workspace, path)
        except ProviderError as error:
            return None, str(error).splitlines()[0]
        if not self._source_is_current(task):
            return None, "that draft's Markdown changed while it was open — reload and try again"
        try:
            parsed = markdown.read(target)
        except OSError as error:
            return None, str(error).splitlines()[0]
        if not parsed.valid:
            return None, "Invalid Markdown drafts cannot be deleted from the TUI"
        return target, ""

    # ---- the query behind the board --------------------------------------

    def query_text(self) -> str:
        return self._query_text

    def can_run_query(self) -> bool:
        """Query support belongs to this provider instance, not the backend class."""
        return bool(self.provider.spec.capabilities.query_language)

    def set_query_text(self, query: str) -> None:
        """Set the session's transient extra provider query clause.

        Search is a read-only view operation. It intentionally does not edit
        ``project.json``; a later normal Sync therefore keeps the workspace's
        configured source unless the user changes that configuration through
        an explicit configuration command.
        """
        if not self.provider.spec.capabilities.query_language:
            return
        self._query_text = query.strip()

    def run_query(self, query: str) -> None:
        """Apply one fresh read-only provider query as an in-memory overlay.

        This deliberately does not call workspace sync: Search must not write
        Markdown/state/Git, prune nonmatches, probe each card with ``get_issue``
        or cross the provider-write boundary.
        """
        if not self.provider.spec.capabilities.query_language:
            raise ProviderError(f"{self.provider.spec.label} has no query language")
        previous = self.query_text()
        value = query.strip()
        previous_results = self._query_results
        if not value:
            try:
                self.set_query_text("")
                self._query_results = None
                self.reload_local()
            except Exception:
                self._query_results = previous_results
                self.set_query_text(previous)
                raise
            return

        # Jira composes its mandatory project clause around this extra JQL.
        # The value remains in memory: Search is a transient view, not a
        # workspace-configuration edit.
        had_configured_jql = "jql" in self.provider.config
        configured_jql = self.provider.config.get("jql")
        self.provider.config["jql"] = value
        try:
            self.provider.refresh()
            results = tuple(self.provider.iter_issues(self.project.project_id))
        finally:
            if had_configured_jql:
                self.provider.config["jql"] = configured_jql
            else:
                self.provider.config.pop("jql", None)
        try:
            self.set_query_text(value)
            self._query_results = results
            self._reload_visible_files()
        except Exception:
            self._query_results = previous_results
            self.set_query_text(previous)
            raise

    def supports_query_language(self) -> str:
        return self.provider.spec.capabilities.query_language


def _issue_from_file(parsed: markdown.IssueFile, issue_id: str) -> RemoteIssue:
    """Build an issue from its markdown alone, for a file never synced.

    The frontmatter is all there is: no snapshot exists to compare against and
    the tracker has never heard of it. Enough to draw a card, which is the
    point -- an unsynced card that cannot be seen is worse than one shown with
    partial detail.
    """
    front = parsed.front
    labels = front.get("labels")
    components = front.get("components")
    return RemoteIssue(
        issue_id=issue_id or str(front.get("key", "") or ""),
        key=str(front.get("key", "") or ""),
        title=str(front.get("title", "") or ""),
        status=str(front.get("status", "") or ""),
        body=parsed.source,
        issue_type=str(front.get("type", "") or ""),
        priority=str(front.get("priority", "") or ""),
        assignee=str(front.get("assignee", "") or ""),
        labels=tuple(str(item) for item in labels) if isinstance(labels, (list, tuple)) else (),
        components=(
            tuple(str(item) for item in components) if isinstance(components, (list, tuple)) else ()
        ),
        url=str(front.get("url", "") or ""),
    )


def _with_local_edits(issue: RemoteIssue, edit: object) -> RemoteIssue:
    """Apply an ``IssueEdit`` for display without changing the baseline."""
    from pykantui.tracker.models import IssueEdit  # noqa: PLC0415 - keeps the public imports small

    if not isinstance(edit, IssueEdit):
        return issue
    updates: dict[str, object] = {}
    for field in edit.touched():
        value = getattr(edit, field)
        if field in edit.cleared:
            value = () if field in {"labels", "components"} else None if field == "due_date" else ""
        updates[field] = value
    return issue.model_copy(update=updates) if updates else issue


def _naive(value: datetime | None) -> datetime | None:
    """Drop the timezone.

    :class:`~pykantui.models.Task` uses naive datetimes throughout, and
    comparing an aware one to a naive one raises. Converting at this boundary
    means the board never has to think about it.
    """
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _file_revision(path: Path) -> str:
    """A compact content fingerprint used only for optimistic local writes."""
    return blake2b(path.read_bytes(), digest_size=16).hexdigest()
