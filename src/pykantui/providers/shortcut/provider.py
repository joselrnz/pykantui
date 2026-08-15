"""Shortcut (formerly Clubhouse).

**Unverified** -- written from the published API, no credentials to run it
against.

Shortcut is a close second to Linear on fit: stories live in **workflow
states**, and each state carries a ``type`` of ``unstarted``, ``started`` or
``done``. That is coarser than Linear's six but still better than a name guess,
and the review column is recovered from the name the way it is for Jira.

The container question is the awkward one. Shortcut has *workflows* (which own
the states), *teams* (called "groups" in the API), *epics* and a deprecated
notion of *projects*. The board people look at is a workflow, so
:meth:`list_projects` returns workflows and the columns are that workflow's own
states -- which means the columns always match the board rather than being a
union of every workflow's states.

Auth is a ``Shortcut-Token`` header. Not ``Authorization`` at all.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

from pykantui.api import TTL_STRUCTURE, JsonObject
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.columns import resolve_group
from pykantui.tracker.errors import NotFoundError, ProviderError
from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_DONE,
    COLUMN_STARTED,
    COLUMN_TODO,
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

from .client import ShortcutApi, ShortcutClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import comment_to_remote, member_to_remote, story_to_remote, workflow_to_remote
from .payloads import create_story_payload, numeric_id, update_story_payload
from .schemas import StoryWire, WorkflowWire

API_URL = "https://api.app.shortcut.com/api/v3"

#: Shortcut's workflow state types.
_STATE_TYPES = {
    "unstarted": COLUMN_TODO,
    "started": COLUMN_STARTED,
    "done": COLUMN_DONE,
    "backlog": COLUMN_BACKLOG,
}

#: How many stories to ask for per search page. Shortcut's maximum is 25.
_PAGE_SIZE = 25


class ShortcutProvider(Provider):
    spec = ProviderSpec(
        name="shortcut",
        label="Shortcut",
        verified=True,  # read, edit and push against a live workspace
        description="Shortcut workflows, states and stories.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        token_url="https://app.shortcut.com/settings/account/api-tokens",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="token",
                label="API token",
                kind=FieldKind.SECRET,
                env_vars=("SHORTCUT_TOKEN", "SHORTCUT_API_TOKEN"),
                help="Settings → API Tokens.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="workflow_id",
                label="Workflow",
                kind=FieldKind.CHOICE,
                env_vars=("SHORTCUT_WORKFLOW_ID",),
                help="A Shortcut workflow is the board.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=True,  # position is a real per-story rank
            create_issues=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "due_date", "issue_type"),
            read_comments=True,
            create_comments=True,
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    @property
    def http(self) -> ShortcutClient:
        if self._http is None:
            self._http = ShortcutClient.connect(
                self.optional("base_url", API_URL).rstrip("/"),
                self.required("token"),
                cache=self.cache,
            )
        return cast(ShortcutClient, self._http)

    @property
    def api(self) -> ShortcutApi:
        """Typed operations over the configured or injected transport."""
        return ShortcutApi(self.http)

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        return member_to_remote(self.api.current_member())

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        """Workflows, which is what a Shortcut board is."""
        return [workflow_to_remote(workflow) for workflow in self.api.workflows()]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        workflow = self._workflow(project_id)
        states = sorted(
            workflow.states,
            key=lambda item: sort_key(item.position),
        )
        return [
            RemoteColumn(
                column_id=str(state.id),
                name=state.name,
                position=position,
                group=_group_for(state.name, state.type),
                status_ids=(str(state.id),),
            )
            for position, state in enumerate(states)
        ]

    def _workflow(self, project_id: str) -> WorkflowWire:
        for workflow in self.api.workflows():
            if str(workflow.id) == str(project_id):
                return workflow
        raise NotFoundError(
            f"no Shortcut workflow {project_id!r}",
            hint="Pick one from the workflow list.",
        )

    # ---- issues ----------------------------------------------------------

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)
        self._member_names: dict[str, str] | None = None

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        """Stories in the workflow, via search.

        Shortcut has no "list every story in a workflow" endpoint, so this goes
        through search with a ``workflow:`` term. Search pages by a ``next``
        *path* -- a full query string, not a cursor to pass back as a
        parameter -- which is why this loop does not use one of the shared
        paging helpers.
        """
        states = {column.column_id: column.name for column in self.list_columns(project_id)}
        workflow = self._workflow(project_id)
        query = f'workflow:"{workflow.name}"'
        for story in self.api.stories(query, page_size=_PAGE_SIZE):
            yield story_to_remote(story, states, self._members())

    def _members(self) -> dict[str, str]:
        """Member id to display name, so owners read as people rather than ids.

        Cached for the run and degrading to an empty directory: a token without
        permission to list members should cost the assignee column, not the
        whole sync. Written from Shortcut's published API and **not verified
        against a live workspace** -- see ``spec.verified``.
        """
        if self._member_names is None:
            try:
                people = self.api.members(ttl=TTL_STRUCTURE)
            except ProviderError:
                people = []
            directory: dict[str, str] = {}
            for person in people:
                profile = person.profile
                name = (profile.name or profile.mention_name) if profile else ""
                if person.id and name:
                    directory[str(person.id)] = name
            self._member_names = directory
        return self._member_names

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One issue as the tracker has it now, for the conflict check.

        Re-read through the same mapping the listing uses: comparing a
        differently-populated copy of an issue against its snapshot invents
        changes that are not there.
        """
        if not issue.issue_id:
            return None
        try:
            story = self.api.story(issue.issue_id)
        except NotFoundError:
            return None
        states = {column.column_id: column.name for column in self.columns(project_id)}
        return story_to_remote(story, states, self._members())

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield Shortcut's complete story comment thread."""

        del project_id
        members = self._members()
        comments = self.api.comments(issue.issue_id)
        if comments and all(comment.position is not None for comment in comments):
            comments.sort(key=lambda comment: (comment.position or 0, comment.created_at or "", str(comment.id)))
        for comment in comments:
            yield comment_to_remote(comment, issue.issue_id, members)

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        comment: CommentDraft,
    ) -> RemoteComment:
        """Append plain text while Shortcut owns author and timestamps."""

        created = self.api.create_comment(issue.issue_id, comment.body)
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        return comment_to_remote(created, issue.issue_id, self._members())

    def _to_issue(
        self, story: Mapping[str, object], states: dict[str, str], members: dict[str, str] | None = None
    ) -> RemoteIssue:
        """Compatibility seam that validates raw story mappings."""
        return story_to_remote(StoryWire.model_validate(story), states, members)

    # ---- writes ----------------------------------------------------------

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        return create_story_payload(draft)

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        story = self.api.create_story(self.build_create_payload(project_id, draft))
        if not story.id:
            raise NotFoundError("Shortcut accepted the story but returned no id")
        states = {column.column_id: column.name for column in self.columns(project_id)}
        return story_to_remote(story, states, self._members())

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self.api.update_story(issue.issue_id, {"workflow_state_id": numeric_id(column.column_id)})

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """One PUT carries every field Shortcut accepts."""
        self.reject_unsupported(edit)
        owner_ids = self._resolve_owner_ids(edit.assignee) if edit.assignee is not None else None
        payload = update_story_payload(edit, owner_ids=owner_ids)
        if payload:
            self.api.update_story(issue.issue_id, payload)

    def _resolve_owner_ids(self, value: str) -> list[str]:
        records = [{"id": member_id, "name": name} for member_id, name in self._members().items()]
        return resolve_ids(records, value, field_label="Shortcut member")


def _group_for(name: str, state_type: str) -> str:
    """Column meaning from Shortcut's state type, with the shared name
    heuristics either side of it. See tracker.columns."""
    return resolve_group(name, type_key=state_type, type_map=_STATE_TYPES)
