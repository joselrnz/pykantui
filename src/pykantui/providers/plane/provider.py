"""Plane (plane.so).

The easiest of the three: one ``X-API-Key`` header, plain REST, no SDK and no
OAuth dance. Verified against a live workspace.

Two details that bite:

* Paging is by cursor, and Plane returns a ``next_cursor`` on *every* page,
  including the last. The loop has to end on ``next_page_results`` being false
  -- ending on "no cursor" never happens and the fetch runs forever. That is
  why :func:`~pykantui.api.pagination.page_by_cursor` keys off the boolean.
* ``state`` is a UUID and its ``name`` is user-editable, so neither is stable
  enough to map columns by. ``state_group`` is the fixed vocabulary
  (``backlog``/``unstarted``/``started``/``completed``/``cancelled``) and is
  what the column mapping actually uses.
"""

from __future__ import annotations

from collections.abc import Iterator
from html import escape
from typing import Any, cast

from pykantui.api import TTL_STRUCTURE, JsonObject
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import NotFoundError, ProviderError
from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_CANCELLED,
    COLUMN_DONE,
    COLUMN_STARTED,
    COLUMN_TODO,
    COLUMN_UNKNOWN,
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
from pykantui.tracker.resolve import resolve_ids
from pykantui.tracker.spec import Capabilities, CredentialSetupKind, FieldKind, ProviderField, ProviderSpec
from pykantui.tracker.util import sort_key

from .client import PlaneApi, PlaneClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import comment_to_remote, project_to_remote, work_item_to_remote
from .payloads import create_work_item_payload, update_work_item_payload
from .schemas import LabelWire, WorkItemWire

#: Plane's own state groups, which are fixed even though state names are not.
_STATE_GROUPS = {
    "backlog": COLUMN_BACKLOG,
    "unstarted": COLUMN_TODO,
    "started": COLUMN_STARTED,
    "completed": COLUMN_DONE,
    "cancelled": COLUMN_CANCELLED,
}

class PlaneProvider(Provider):
    issue_cache_labels = ("work items",)
    spec = ProviderSpec(
        name="plane",
        label="Plane",
        description="Plane — open-source project management, cloud or self-hosted.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        verified=True,  # exercised against a live instance
        token_url="https://app.plane.so/profile/api-tokens",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="base_url",
                label="API URL",
                kind=FieldKind.URL,
                required=False,
                default="https://api.plane.so",
                placeholder="https://api.plane.so",
                env_vars=("PLANE_BASE_URL",),
                help="Change this only for a self-hosted Plane.",
            ),
            ProviderField(
                name="token",
                label="API key",
                kind=FieldKind.SECRET,
                env_vars=("PLANE_TOKEN", "PLANE_API_KEY"),
                help="Workspace settings → API tokens. Starts with plane_api_.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="workspace",
                label="Workspace slug",
                placeholder="acme",
                env_vars=("PLANE_WORKSPACE",),
                help="The first path segment of your Plane URL.",
            ),
            ProviderField(
                name="project_id",
                label="Project",
                kind=FieldKind.CHOICE,
                env_vars=("PLANE_PROJECT_ID",),
                help="Plane identifies projects by UUID.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=True,  # sort_order is a real client-side ordering
            create_issues=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "due_date", "priority"),
            backlog=True,  # Plane has a genuine backlog state group
            read_comments=True,
            create_comments=True,
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    DEFAULT_BASE_URL = "https://api.plane.so"

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)
        self._members_by_project: dict[str, dict[str, str]] = {}
        self._label_cache: dict[str, str] | None = None

    @property
    def http(self) -> PlaneClient:
        if self._http is None:
            self._http = PlaneClient.connect(
                self.optional("base_url", self.DEFAULT_BASE_URL).rstrip("/"),
                self.required("token"),
                cache=self.cache,
            )
        return cast(PlaneClient, self._http)

    @property
    def api(self) -> PlaneApi:
        """Typed operations scoped to the configured workspace."""
        return PlaneApi(self.http, self.required("workspace"))

    def _workspace_path(self, *parts: str) -> str:
        from .routes import workspace  # noqa: PLC0415 - compatibility helper

        return workspace(self.required("workspace"), *parts)

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        """Confirm the key by listing projects.

        Plane's API tokens are workspace-scoped and there is no ``/me`` for
        them, so reachability of the workspace *is* the check that matters --
        it catches a wrong slug as well as a wrong key.
        """
        self.api.verify_workspace()
        return RemoteUser(account_id=self.required("workspace"), display_name=self.required("workspace"))

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        workspace = self.required("workspace")
        api_base_url = self.optional("base_url", self.DEFAULT_BASE_URL)

        return [
            project_to_remote(item, workspace, api_base_url=api_base_url)
            for item in self.api.projects()
        ]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        states = list(self.api.states(project_id, ttl=TTL_STRUCTURE))
        states.sort(key=lambda item: sort_key(item.sequence))
        return [
            RemoteColumn(
                column_id=state.id,
                name=state.name,
                position=position,
                group=_STATE_GROUPS.get(state.group, COLUMN_UNKNOWN),
                status_ids=(state.id,),
            )
            for position, state in enumerate(states)
        ]

    # ---- issues ----------------------------------------------------------

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        workspace = self.required("workspace")
        identifier = self._project_identifier(project_id)
        states = {column.column_id: column.name for column in self.list_columns(project_id)}
        members = self._member_names(project_id)

        for item in self.api.work_items(project_id, ttl=self.issue_ttl):
            yield self._mapped_issue(item, workspace, project_id, identifier, states, members)

    def _to_issue(
        self,
        raw: dict[str, Any],
        workspace: str,
        project_id: str,
        identifier: str,
        states: dict[str, str],
        members: dict[str, str],
    ) -> RemoteIssue:
        """Compatibility seam that validates a raw work-item mapping."""
        return self._mapped_issue(
            WorkItemWire.model_validate(raw), workspace, project_id, identifier, states, members
        )

    def _mapped_issue(
        self,
        item: WorkItemWire,
        workspace: str,
        project_id: str,
        identifier: str,
        states: dict[str, str],
        members: dict[str, str],
    ) -> RemoteIssue:
        return work_item_to_remote(
            item,
            workspace=workspace,
            project_id=project_id,
            identifier=identifier,
            states=states,
            members=members,
            labels=self._label_names(project_id, item.labels),
            api_base_url=self.optional("base_url", self.DEFAULT_BASE_URL),
        )

    def _label_names(self, project_id: str, raw: Any) -> list[str]:
        """Label names, from either shape Plane returns them in.

        The two endpoints disagree. Listing issues gives bare UUID strings::

            "labels": ["5640167d-007e-4934-a025-fada65bbf286"]

        Fetching one issue gives objects::

            "labels": [{"id": "5640167d-…", "name": "concepts", …}]

        Left alone, that made every labelled issue look changed the moment the
        two were compared -- so the pre-push conflict check reported a conflict
        on an issue nobody had touched. It also put a UUID in the markdown,
        where a human wanted to read "concepts".

        Both shapes are resolved through the project's label directory, which
        is cached like the columns are.
        """
        if not isinstance(raw, (list, tuple)) or not raw:
            # Nothing to resolve. Returning here also avoids fetching the label
            # directory for a project whose issues carry no labels at all.
            return []

        # Objects already carry their name; only bare ids need the directory.
        if all(
            (isinstance(label, LabelWire) and label.name)
            or (isinstance(label, dict) and label.get("name"))
            for label in raw
        ):
            return [label.name if isinstance(label, LabelWire) else str(label["name"]) for label in raw]

        directory = self._labels(project_id)
        found: list[str] = []
        for label in raw:
            if isinstance(label, LabelWire):
                name = label.name or directory.get(label.id, "")
            elif isinstance(label, dict):
                name = str(label.get("name") or directory.get(str(label.get("id", "")), "") or "")
            else:
                name = directory.get(str(label), "") or str(label)
            if name:
                found.append(name)
        return found

    def _labels(self, project_id: str) -> dict[str, str]:
        """Map label id -> name. Fetched once per run, cached for six hours."""
        if self._label_cache is not None:
            return self._label_cache

        found = {row.id: row.name for row in self.api.labels(project_id, ttl=TTL_STRUCTURE) if row.id and row.name}
        self._label_cache = found
        return found

    def _project_identifier(self, project_id: str) -> str:
        """The short prefix Plane puts in front of an issue number."""
        try:
            project = self.api.project(project_id, ttl=TTL_STRUCTURE)
        except Exception:  # noqa: BLE001 - a missing prefix is cosmetic, not fatal
            return ""
        return project.identifier

    def _member_names(self, project_id: str) -> dict[str, str]:
        """Map member UUIDs to display names.

        Issues carry assignee UUIDs only, and a file full of
        ``assignee: 4f2c...`` helps nobody. Fetched once per sync and cached.
        """
        cached = self._members_by_project.get(project_id)
        if cached is not None:
            return cached

        found: dict[str, str] = {}
        for record in self.api.members(project_id, ttl=TTL_STRUCTURE):
            member = record.member or record
            identifier = member.id
            # Prefer the name people see in Plane. Email remains the fallback
            # for accounts whose profile has no display name.
            label = member.display_name or member.email
            if identifier and label:
                found[identifier] = label

        self._members_by_project[project_id] = found
        return found

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One issue, for the pre-push conflict check. Never cached."""
        if not issue.issue_id:
            return None
        try:
            item = self.api.work_item(project_id, issue.issue_id)
        except NotFoundError:
            return None
        identifier = self._project_identifier(project_id)
        states = {column.column_id: column.name for column in self.columns(project_id)}
        return self._mapped_issue(
            item, self.required("workspace"), project_id, identifier, states, self._member_names(project_id)
        )

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield all comments through Plane's current work-items API."""

        members = self._member_names(project_id)
        for comment in self.api.comments(project_id, issue.issue_id):
            yield comment_to_remote(comment, issue.issue_id, members)

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        comment: CommentDraft,
    ) -> RemoteComment:
        """Append escaped HTML without interpreting local Markdown as markup."""

        safe_html = "<p>" + escape(comment.body).replace("\n", "<br>") + "</p>"
        created = self.api.create_comment(
            project_id,
            issue.issue_id,
            {"comment_html": safe_html, "access": "INTERNAL"},
        )
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        return comment_to_remote(created, issue.issue_id, self._member_names(project_id))

    # ---- writes ----------------------------------------------------------

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self._patch(issue, {"state": column.column_id})

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        """None, on purpose.

        Plane does have issue types, but the endpoint
        ``/projects/{id}/issue-types/`` answers **402 Payment required** on the
        free plan -- measured, not assumed. There is no free-plan equivalent,
        so the honest answer is an empty list, which callers read as "send no
        type" rather than as an error.

        The create below therefore sends no type at all, and a ``--type`` the
        user passes is carried in the markdown without being invented into a
        field Plane would reject.
        """
        return []

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        """One POST. Plane returns the created issue in full, so no re-read."""
        body = self.build_create_payload(project_id, draft)

        item = self.api.create_work_item(project_id, body)
        if not item.id:
            raise ProviderError("Plane accepted the issue but returned no id")

        return self._mapped_issue(
            item,
            self.required("workspace"),
            project_id,
            self._project_identifier(project_id),
            {c.column_id: c.name for c in self.columns(project_id)},
            self._member_names(project_id),
        )

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        label_ids = self._resolve_label_ids(draft.labels) if draft.labels else []
        return create_work_item_payload(draft, label_ids=label_ids)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """One PATCH carries the whole edit.

        Plane accepts state, name and description together, so unlike Jira
        this never has to split a column change out into a second request.
        """
        self.reject_unsupported(edit)

        member_ids = self._resolve_member_ids(edit.assignee) if edit.assignee is not None else None
        label_ids = self._resolve_label_ids(edit.labels) if edit.labels is not None else None
        body = update_work_item_payload(edit, member_ids=member_ids, label_ids=label_ids)
        if body:
            self._patch(issue, body)

    def _resolve_member_ids(self, value: str) -> list[str]:
        project_id = self.required("project_id")
        records = [{"id": member_id, "name": name} for member_id, name in self._member_names(project_id).items()]
        return resolve_ids(records, value, field_label="Plane member")

    def _resolve_label_ids(self, values: tuple[str, ...]) -> list[str]:
        records = [
            {"id": label_id, "name": name} for label_id, name in self._labels(self.required("project_id")).items()
        ]
        return resolve_ids(records, values, name_keys=("name",), field_label="Plane label")

    def _patch(self, issue: RemoteIssue, body: JsonObject) -> None:
        self.api.update_work_item(self.required("project_id"), issue.issue_id, body)
