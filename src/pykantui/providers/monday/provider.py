"""Monday.com.

**Unverified.** Like Trello, written from the published API without credentials
to run it against. Jira and Plane in this package were built against live
instances; treat this mapping as a first draft.

Monday is the odd one of the four, in three ways:

* **GraphQL, not REST.** Every call is a POST to one endpoint. That means a
  failed query returns **HTTP 200** with an ``errors`` array, which is why
  :meth:`~pykantui.api.client.JsonHttp.graphql` inspects the payload instead
  of trusting the status code.
* **The auth header carries the token bare** -- no ``Bearer`` prefix, no basic
  auth. Just ``Authorization: <token>``.
* **A board has both groups and columns, and neither is obviously "the"
  kanban column.** Groups are ordered sections of rows; a *status column* is a
  field whose values are the labels people actually drag cards between. Monday's
  own Kanban view groups by a status column, so that is what this uses, falling
  back to groups where a board has no status column at all. Set
  ``status_column`` to choose a specific one on a board that has several.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from html import escape
from typing import Any, cast

from pykantui.api import JsonObject
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.columns import group_from_name
from pykantui.tracker.errors import NotFoundError
from pykantui.tracker.models import (
    CommentDraft,
    IssueDraft,
    IssueEdit,
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.resolve import resolve_ids
from pykantui.tracker.spec import Capabilities, CredentialSetupKind, FieldKind, ProviderField, ProviderSpec
from pykantui.tracker.util import sort_key

from .client import MondayApi, MondayClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import board_to_remote, item_to_remote, labels_from, update_to_remote, user_to_remote
from .payloads import as_int, create_item_variables, update_column_values
from .schemas import BoardShapeWire, ItemWire

API_URL = "https://api.monday.com/v2"

#: Monday versions its GraphQL schema by date and warns loudly when a request
#: does not pin one. Pinned rather than left to the default so that a
#: server-side default moving forward cannot silently change our field shapes.
API_VERSION = "2026-07"

_COLUMN_CONFIG = {
    "body": "description_column",
    "assignee": "assignee_column",
    "issue_type": "type_column",
    "priority": "priority_column",
    "labels": "labels_column",
    "due_date": "due_column",
}

class MondayProvider(Provider):
    spec = ProviderSpec(
        name="monday",
        label="Monday.com",
        verified=True,  # read, edit and push against a live board
        description="Monday.com boards, groups and items.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        token_url="https://monday.com/developers/v2",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="token",
                label="API token",
                kind=FieldKind.SECRET,
                env_vars=("MONDAY_TOKEN", "MONDAY_API_TOKEN"),
                help="Profile → Developers → My access tokens.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="board_id",
                label="Board",
                kind=FieldKind.CHOICE,
                env_vars=("MONDAY_BOARD_ID",),
            ),
            ProviderField(
                name="status_column",
                label="Status column id",
                required=False,
                env_vars=("MONDAY_STATUS_COLUMN",),
                help="Optional. Which status column is the kanban axis, on a board with several.",
            ),
            ProviderField(
                name="description_column",
                label="Description column id",
                required=False,
                env_vars=("MONDAY_DESCRIPTION_COLUMN",),
            ),
            ProviderField(
                name="assignee_column", label="People column id", required=False, env_vars=("MONDAY_ASSIGNEE_COLUMN",)
            ),
            ProviderField(name="type_column", label="Type column id", required=False, env_vars=("MONDAY_TYPE_COLUMN",)),
            ProviderField(
                name="priority_column", label="Priority column id", required=False, env_vars=("MONDAY_PRIORITY_COLUMN",)
            ),
            ProviderField(
                name="labels_column", label="Labels column id", required=False, env_vars=("MONDAY_LABELS_COLUMN",)
            ),
            ProviderField(
                name="due_column", label="Due date column id", required=False, env_vars=("MONDAY_DUE_COLUMN",)
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=False,  # Monday orders by group membership, not a per-item rank we can set
            create_issues=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "due_date", "priority", "issue_type"),
            read_comments=True,
            create_comments=True,
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)
        #: Cached per sync: which column is the status axis, and its labels.
        self._axis: tuple[str, dict[str, str]] | None = None

    @property
    def http(self) -> MondayClient:
        if self._http is None:
            self._http = MondayClient.connect(
                self.optional("base_url", API_URL),
                self.required("token"),
                api_version=API_VERSION,
                cache=self.cache,
            )
        return cast(MondayClient, self._http)

    @property
    def api(self) -> MondayApi:
        """Return the typed Monday operation facade."""
        return MondayApi(self.http)

    def _provider_columns(self) -> dict[str, str]:
        """Return configured provider column ids keyed by config name."""
        return {
            key: self.optional(key)
            for key in ("status_column", *_COLUMN_CONFIG.values())
        }

    def _semantic_columns(self) -> dict[str, str]:
        """Return configured column ids keyed by neutral field name."""
        return {
            field: self.optional(config_key)
            for field, config_key in _COLUMN_CONFIG.items()
            if self.optional(config_key)
        }

    def _edit_column_config(self, edit: IssueEdit) -> dict[str, str]:
        """Require only provider columns touched by this edit."""
        configured = self._provider_columns()
        for field, config_key in _COLUMN_CONFIG.items():
            if getattr(edit, field) is not None or field in edit.cleared:
                configured[config_key] = self.required(config_key)
        return configured

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        return user_to_remote(self.api.viewer())

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        """Monday boards, presented as projects.

        Paged by page number rather than a cursor -- ``boards`` is one of the
        older parts of the schema and never gained an ``items_page``-style
        connection. Ends on a short page.
        """
        return [board_to_remote(board) for board in self.api.boards()]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        column_id, labels = self._status_axis(project_id)
        if column_id:
            return [
                RemoteColumn(
                    column_id=index,
                    name=label,
                    position=position,
                    group=_group_for(label),
                    status_ids=(index,),
                )
                for position, (index, label) in enumerate(labels.items())
            ]
        return self._group_columns(project_id)

    def _group_columns(self, project_id: str) -> list[RemoteColumn]:
        """Fall back to the board's groups where there is no status column."""
        board = self._board_shape(project_id)
        groups = sorted(
            board.groups,
            key=lambda item: sort_key(item.position),
        )
        return [
            RemoteColumn(
                column_id=group.id,
                name=group.title,
                position=position,
                group=_group_for(group.title),
                status_ids=(group.id,),
            )
            for position, group in enumerate(groups)
        ]

    def _board_shape(self, project_id: str) -> BoardShapeWire:
        board = self.api.board_shape(project_id)
        if board is None:
            raise NotFoundError(
                f"no Monday board {project_id!r}",
                hint="Check the id, or pick one from the board list.",
            )
        return board

    def _status_axis(self, project_id: str) -> tuple[str, dict[str, str]]:
        """The status column the board is grouped by, and its labels.

        Returns ``("", {})`` where the board has no status column, which is the
        signal to fall back to groups.
        """
        if self._axis is not None:
            return self._axis

        wanted = self.optional("status_column")
        columns = self._board_shape(project_id).columns
        chosen = None
        for column in columns:
            if wanted and column.id == wanted:
                chosen = column
                break
            if not wanted and column.type in ("status", "color"):
                chosen = column
                break

        if chosen is None:
            self._axis = ("", {})
            return self._axis

        self._axis = (chosen.id, labels_from(chosen.settings_str))
        return self._axis

    # ---- issues ----------------------------------------------------------

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        axis_id, labels = self._status_axis(project_id)
        for item in self.api.items(project_id):
            yield item_to_remote(
                item,
                project_id,
                axis_id,
                labels,
                self._semantic_columns(),
            )

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One issue as the tracker has it now, for the conflict check.

        Re-read through the same mapping the listing uses: comparing a
        differently-populated copy of an issue against its snapshot invents
        changes that are not there.
        """
        if not issue.issue_id:
            return None
        item = self.api.item(issue.issue_id)
        if item is None:
            return None
        axis_id, labels = self._status_axis(project_id)
        return item_to_remote(
            item,
            project_id,
            axis_id,
            labels,
            self._semantic_columns(),
        )

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield a Monday update thread in chronological order."""

        del project_id
        comments: list[RemoteComment] = []
        for update in self.api.updates(issue.issue_id):
            comments.append(update_to_remote(update, issue.issue_id))
            comments.extend(
                update_to_remote(reply, issue.issue_id, parent_id=str(update.id))
                for reply in update.replies
            )
        yield from sorted(
            comments,
            key=lambda comment: (comment.created_at is None, comment.created_at, comment.comment_id),
        )

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        comment: CommentDraft,
    ) -> RemoteComment:
        """Append an escaped HTML update and use Monday's canonical response."""

        safe_html = "<p>" + escape(comment.body).replace("\n", "<br>") + "</p>"
        created = self.api.create_update(issue.issue_id, safe_html)
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        return update_to_remote(created, issue.issue_id)

    def _to_issue(
        self,
        item: Mapping[str, object],
        project_id: str,
        axis_id: str,
        labels: dict[str, str],
    ) -> RemoteIssue:
        """Compatibility mapper used by provider-level mapping tests."""
        return item_to_remote(
            ItemWire.model_validate(item),
            project_id,
            axis_id,
            labels,
            self._semantic_columns(),
        )

    # ---- writes ----------------------------------------------------------

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        configured = self._provider_columns()
        if not configured["status_column"]:
            configured["status_column"] = self._status_axis(project_id)[0]
        return create_item_variables(draft, project_id, configured)

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        issue_id = self.api.create_item(self.build_create_payload(project_id, draft))
        if not issue_id:
            raise NotFoundError("Monday.com accepted the item but returned no id")
        item = self.api.item(issue_id)
        if item is None:
            raise NotFoundError(f"Monday.com created item {issue_id} but could not read it back")
        axis_id, labels = self._status_axis(project_id)
        return item_to_remote(
            item,
            project_id,
            axis_id,
            labels,
            self._semantic_columns(),
        )

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """Rename and move. Monday needs a typed mutation per kind of change.

        Deliberately narrow: Monday has no general "description" field -- a
        body lives in whichever ``long_text`` column the board happens to
        define, if any -- so pushing one back would mean guessing at a column
        id. ``writable_fields`` says so rather than guessing.
        """
        self.reject_unsupported(edit)
        values = update_column_values(
            edit,
            self._edit_column_config(edit),
            assignee_ids=self._resolve_people_ids(edit.assignee)
            if edit.assignee is not None
            else (),
        )
        if values:
            self.api.change_columns(
                issue.issue_id,
                self.required("board_id"),
                json.dumps(values),
            )
        if edit.title is not None:
            # `change_simple_column_value` takes a plain String; it is
            # `change_column_value` that takes JSON. Declaring JSON here made
            # Monday reject every rename with a type error.
            self.api.rename_item(issue.issue_id, self.required("board_id"), edit.title)
        if edit.column_id is not None:
            self.move_issue(issue, RemoteColumn(column_id=edit.column_id, name=edit.column_id))

    def _resolve_people_ids(self, value: str) -> list[int]:
        users = [user.model_dump() for user in self.api.users()]
        return [int(item) for item in resolve_ids(users, value, field_label="Monday.com user")]

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        board_id = self.required("board_id")
        axis_id, _ = self._status_axis(board_id)
        if not axis_id:
            # Groups, not a status column: a different mutation entirely.
            self.api.move_to_group(issue.issue_id, column.column_id)
            return
        self.api.move_to_status(
            issue.issue_id,
            board_id,
            axis_id,
            json.dumps({"index": as_int(column.column_id)}),
        )

def _group_for(name: str) -> str:
    """Column meaning from the name alone; this tracker types nothing."""
    return group_from_name(name)
