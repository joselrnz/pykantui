"""Asana.

**Unverified** -- written from the published API, no credentials to run it
against.

Asana's board view is backed by **sections**, and a task's section comes from
its ``memberships`` array rather than from a field on the task: a task can
belong to several projects, and it has a section in each. So the section has to
be picked out by matching the project we are syncing, which is what
:meth:`_section_of` does. Reading ``memberships[0]`` would put a card in
whatever project Asana happened to list first.

Two more things:

* **Everything is opt-in.** Asana returns a bare minimum of fields unless asked
  via ``opt_fields``, so an omission there is a field silently missing from
  every exported file rather than an error.
* **Ids are strings called ``gid``**, and they are not interchangeable with the
  numeric ids in older documentation.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

from pykantui.api import TTL_STRUCTURE, JsonObject, QueryValue
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.columns import group_from_name
from pykantui.tracker.errors import NotFoundError
from pykantui.tracker.models import (
    ColumnGroup,
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

from .client import AsanaApi, AsanaClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import project_to_remote, story_to_remote, task_to_remote, user_to_remote
from .payloads import create_comment_payload, create_task_payload, update_task_payload
from .schemas import TaskWire

API_URL = "https://app.asana.com/api/1.0"

#: Asked for explicitly, because Asana sends almost nothing by default.
TASK_FIELDS = (
    "name,notes,completed,completed_at,created_at,modified_at,due_on,permalink_url,"
    "assignee.name,assignee.gid,created_by.name,created_by.gid,"
    "tags.name,parent.name,memberships.section.gid,memberships.section.name,"
    "memberships.project.gid"
)

STORY_FIELDS = "gid,resource_subtype,type,text,html_text,created_at,created_by.gid,created_by.name"


class AsanaProvider(Provider):
    spec = ProviderSpec(
        name="asana",
        label="Asana",
        verified=True,  # read, edit and push against a live workspace
        description="Asana projects, sections and tasks.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        token_url="https://app.asana.com/0/my-apps",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="token",
                label="Personal access token",
                kind=FieldKind.SECRET,
                env_vars=("ASANA_TOKEN", "ASANA_ACCESS_TOKEN"),
                help="My Settings → Apps → Manage developer apps.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="project_id",
                label="Project",
                kind=FieldKind.CHOICE,
                env_vars=("ASANA_PROJECT_ID",),
                help="Asana calls this a gid.",
            ),
            ProviderField(
                name="workspace",
                label="Workspace gid",
                required=False,
                env_vars=("ASANA_WORKSPACE",),
                help="Optional. Narrows the project list on a multi-workspace account.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=False,
            create_issues=True,
            read_comments=True,
            create_comments=True,
            writable_fields=("title", "body", "column_id", "assignee", "due_date"),
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)

        #: Looked up once when `workspace` was not configured. Keep every
        #: visible scope: selecting the first silently hides valid projects.
        self._found_workspaces: tuple[str, ...] | None = None

    @property
    def http(self) -> AsanaClient:
        if self._http is None:
            self._http = AsanaClient.connect(
                self.optional("base_url", API_URL).rstrip("/"), self.required("token"), cache=self.cache
            )
        return cast(AsanaClient, self._http)

    @property
    def api(self) -> AsanaApi:
        """Typed operations over the configured or injected transport."""
        return AsanaApi(self.http)

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        return user_to_remote(self.api.current_user())

    # ---- projects and columns -------------------------------------------

    def _workspaces(self) -> tuple[str, ...]:
        """Every Asana workspace in the configured discovery scope.

        ``/projects`` requires one workspace. An explicit setting remains a
        useful narrowing option; without it, every workspace visible to the
        token is queried so the wizard can offer the complete project list.
        """
        configured = self.optional("workspace")
        if configured:
            return (configured,)
        if self._found_workspaces is None:
            spaces = self.api.workspaces(ttl=TTL_STRUCTURE)
            self._found_workspaces = tuple(space.gid for space in spaces if space.gid)
        return self._found_workspaces

    def list_projects(self) -> list[RemoteProject]:
        found: list[RemoteProject] = []
        for workspace in self._workspaces():
            params: dict[str, QueryValue] = {
                "opt_fields": "name,notes,permalink_url,archived,workspace.gid,workspace.name",
                "workspace": workspace,
            }
            found.extend(
                project_to_remote(project)
                for project in self.api.projects(params)
                if not project.archived
            )
        return found

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        sections = self.api.sections(project_id)
        return [
            RemoteColumn(
                column_id=section.gid,
                name=section.name,
                position=position,
                group=_group_for(section.name),
                status_ids=(section.gid,),
            )
            for position, section in enumerate(sections)
        ]

    # ---- issues ----------------------------------------------------------

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        for task in self.api.tasks(project_id, fields=TASK_FIELDS):
            yield task_to_remote(task, project_id)

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One issue as the tracker has it now, for the conflict check.

        Re-read through the same mapping the listing uses: comparing a
        differently-populated copy of an issue against its snapshot invents
        changes that are not there.
        """
        if not issue.issue_id:
            return None
        try:
            task = self.api.task(issue.issue_id, fields=TASK_FIELDS)
        except NotFoundError:
            return None
        return task_to_remote(task, project_id)

    def _to_issue(self, task: Mapping[str, object], project_id: str) -> RemoteIssue:
        """Compatibility seam for tests and integrations using raw task maps."""
        return task_to_remote(TaskWire.model_validate(task), project_id)

    # ---- comments --------------------------------------------------------

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield user-authored comments, excluding Asana's system stories."""
        del project_id
        if not issue.issue_id:
            return
        comments: list[RemoteComment] = []
        for story in self.api.stories(issue.issue_id, fields=STORY_FIELDS):
            if story.resource_subtype != "comment_added":
                continue
            comments.append(story_to_remote(story, issue_id=issue.issue_id, issue_url=issue.url))
        yield from sorted(
            comments,
            key=lambda comment: (comment.created_at is None, comment.created_at, comment.comment_id),
        )

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        draft: CommentDraft,
    ) -> RemoteComment:
        """Append one Asana story and return the server-authored record."""
        if not issue.issue_id:
            raise NotFoundError("Asana task has no gid")
        story = self.api.create_story(
            issue.issue_id,
            create_comment_payload(draft),
            fields=STORY_FIELDS,
        )
        created = story_to_remote(story, issue_id=issue.issue_id, issue_url=issue.url)
        self.invalidate_comment_cache(project_id, issue.issue_id)
        return created

    # ---- writes ----------------------------------------------------------

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        return create_task_payload(project_id, draft)

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        created = self.api.create_task(self.build_create_payload(project_id, draft))
        task_id = created.gid
        if not task_id:
            raise NotFoundError("Asana accepted the task but returned no gid")
        if draft.column_id:
            self.api.add_task_to_section(draft.column_id, task_id)
        task = self.api.task(task_id, fields=TASK_FIELDS)
        if not task.gid:
            raise NotFoundError(f"Asana created task {task_id} but could not read it back")
        return task_to_remote(task, project_id)

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self.api.add_task_to_section(column.column_id, issue.issue_id)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """A PUT for the fields, and a separate call for the section.

        Asana moves a task between sections with ``addTask`` rather than by
        setting a field, so a column change cannot ride along with the rest.
        """
        self.reject_unsupported(edit)
        assignee_id = self._resolve_assignee_id(edit.assignee) if edit.assignee is not None else None
        data = update_task_payload(edit, assignee_id=assignee_id)
        if data:
            self.api.update_task(issue.issue_id, data)
        if edit.column_id:
            self.api.add_task_to_section(edit.column_id, issue.issue_id)

    def _resolve_assignee_id(self, value: str) -> str:
        workspace = self.optional("workspace")
        if not workspace:
            project = self.api.project(self.required("project_id"))
            workspace = project.workspace.gid if project.workspace else ""
        users = [user.model_dump() for user in self.api.workspace_users(workspace)]
        return resolve_ids(users, value, id_key="gid", field_label="Asana user")[0]


def _group_for(name: str) -> ColumnGroup:
    """Column meaning from the name alone; this tracker types nothing."""
    return group_from_name(name)
