"""Jira Cloud.

Two things here are not what a reasonable person would guess, and both were
measured against a live instance rather than read off a doc page:

**Auth is basic, not bearer.** A Jira *Cloud* API token authenticates as
``email:token`` over HTTP basic. The bearer-token scheme belongs to Jira
*Server* / Data Center personal access tokens. The two tokens look alike and
fail alike, so the error hint in :mod:`pykantui.api.client` names it
explicitly. Measured against a live Cloud site: bearer → 403, basic → 200.

**Search moved.** ``/rest/api/2/search`` and ``/rest/api/3/search`` have been
*removed* from Cloud (Atlassian CHANGE-2046), not merely deprecated -- they
answer with an error telling you to migrate. The replacement is
``/rest/api/3/search/jql``, which pages by opaque token and no longer returns a
``total`` at all. Any code that pages by ``startAt`` or trusts ``total`` reads
zero issues and reports success.
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
    COLUMN_DONE,
    COLUMN_STARTED,
    COLUMN_TODO,
    ColumnGroup,
    CommentDraft,
    IssueComponent,
    IssueDraft,
    IssueEdit,
    IssueType,
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.spec import Capabilities, CredentialSetupKind, FieldKind, ProviderField, ProviderSpec

from .client import JiraApi, JiraClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import comment_to_remote, issue_to_remote, project_to_remote, user_to_remote
from .payloads import comment_document, create_issue_fields, update_issue_fields
from .schemas import IssueWire

#: Everything the exporter needs, named explicitly. The new search endpoint
#: returns only ``id`` and ``key`` unless asked, so an omission here is a field
#: silently missing from every markdown file.
ISSUE_FIELDS = (
    "summary,description,status,issuetype,assignee,reporter,labels,priority,"
    "components,created,updated,duedate,parent,resolutiondate"
)

#: Jira's own coarse grouping, on every status regardless of workflow.
_CATEGORY_GROUPS = {
    "new": COLUMN_TODO,
    "indeterminate": COLUMN_STARTED,
    "done": COLUMN_DONE,
}


class JiraProvider(Provider):
    spec = ProviderSpec(
        name="jira",
        label="Jira",
        description="Atlassian Jira Cloud — projects, boards and sprints.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        verified=True,  # exercised against a live instance
        token_url="https://id.atlassian.com/manage-profile/security/api-tokens",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="base_url",
                label="Site URL",
                kind=FieldKind.URL,
                placeholder="https://your-site.atlassian.net",
                env_vars=("JIRA_BASE_URL",),
                help="Your Atlassian site, without a path.",
            ),
            ProviderField(
                name="email",
                label="Account email",
                placeholder="you@example.com",
                env_vars=("JIRA_EMAIL",),
                help="Jira Cloud signs API requests with your email and the token together.",
            ),
            ProviderField(
                name="token",
                label="API token",
                kind=FieldKind.SECRET,
                env_vars=("JIRA_TOKEN", "JIRA_API_TOKEN"),
                help="Created at id.atlassian.com — not your account password.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="project_key",
                label="Project key",
                kind=FieldKind.CHOICE,
                placeholder="JPT",
                env_vars=("JIRA_PROJECT_KEY",),
            ),
            ProviderField(
                name="board_id",
                label="Board id",
                kind=FieldKind.INTEGER,
                required=False,
                env_vars=("JIRA_BOARD_ID",),
                help="Optional. Takes the column layout from this board instead of the raw statuses.",
            ),
            ProviderField(
                name="jql",
                label="Extra JQL",
                required=False,
                help="Optional. ANDed with the project filter to narrow what gets pulled.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=False,  # Jira has no client-side row order.
            create_issues=True,
            parent_issues=True,
            writable_fields=(
                "title",
                "body",
                "column_id",
                "assignee",
                "labels",
                "due_date",
                "priority",
                "issue_type",
                "components",
            ),
            query_language="JQL",
            backlog=False,  # Team-managed boards have no backlog endpoint; see _board_issues.
            read_comments=True,
            create_comments=True,
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)
        self._transitions: dict[str, dict[str, str]] = {}

    # ---- connection ------------------------------------------------------

    @property
    def http(self) -> JiraClient:
        if self._http is None:
            self._http = JiraClient.connect(
                self.required("base_url").rstrip("/"),
                self.required("email"),
                self.required("token"),
                cache=self.cache,
            )
        return cast(JiraClient, self._http)

    @property
    def api(self) -> JiraApi:
        """Typed operations over the configured or injected transport."""
        return JiraApi(self.http)

    def verify(self) -> RemoteUser:
        return user_to_remote(self.api.current_user())

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        base = self.required("base_url").rstrip("/")
        return [project_to_remote(item, base) for item in self.api.projects(ttl=TTL_STRUCTURE)]

    def list_boards(self) -> list[JsonObject]:
        """Scrum and kanban boards, for the wizard's board picker."""

        return [board.model_dump() for board in self.api.boards()]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        """Columns from the board where one is configured, else from the statuses.

        A board's column layout is what the user actually looks at -- several
        statuses can sit under one column -- so it wins when we have a board id.
        """
        board_id = self.optional("board_id")
        if board_id:
            columns = self._board_columns(board_id)
            if columns:
                return columns
        return self._status_columns(project_id)

    def _board_columns(self, board_id: str) -> list[RemoteColumn]:
        try:
            document = self.api.board_configuration(board_id, ttl=TTL_STRUCTURE)
        except NotFoundError:
            return []
        columns: list[RemoteColumn] = []
        for position, column in enumerate(document.columnConfig.columns):
            statuses = tuple(status.id for status in column.statuses if status.id)
            name = column.name
            if not statuses:
                # A board column with no status behind it cannot hold an issue.
                continue
            columns.append(
                RemoteColumn(
                    column_id=statuses[0],
                    name=name,
                    position=position,
                    group=_group_for(name, ""),
                    status_ids=statuses,
                )
            )
        return columns

    def _status_columns(self, project_key: str) -> list[RemoteColumn]:
        try:
            document = self.api.project_statuses(project_key, ttl=TTL_STRUCTURE)
        except NotFoundError as error:
            raise NotFoundError(
                f"no Jira project {project_key!r}",
                hint="Check the key, or list what you can see with the project picker.",
            ) from error

        seen: dict[str, RemoteColumn] = {}
        for issue_type in document.root:
            for status in issue_type.statuses:
                status_id = status.id
                if status_id in seen:
                    continue
                name = status.name
                category = status.statusCategory.key if status.statusCategory else ""
                seen[status_id] = RemoteColumn(
                    column_id=status_id,
                    name=name,
                    position=len(seen),
                    group=_group_for(name, category),
                    status_ids=(status_id,),
                )
        return list(seen.values())

    # ---- issues ----------------------------------------------------------

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        """Every issue in the project.

        Sourced from JQL rather than the board's backlog endpoint on purpose:
        a team-managed board answers ``/backlog`` with "Backlogs are not
        supported on this board", and even where it works it omits everything
        already pulled into the sprint. ``project = KEY`` is the only query that
        genuinely means "all of it" on every board type.
        """
        jql = self._jql(project_id)
        base = self.required("base_url").rstrip("/")
        for issue in self.api.issues(jql, fields=ISSUE_FIELDS, ttl=self.issue_ttl):
            yield issue_to_remote(issue, base)

    def _jql(self, project_key: str) -> str:
        extra = self.optional("jql")
        clause = f"project = {project_key}"
        if extra:
            clause = f"{clause} AND ({extra})"
        # A stable sort makes the export deterministic, which is what keeps a
        # re-run from producing a diff made entirely of reordering.
        return f"{clause} ORDER BY key ASC"

    def _to_issue(self, raw: Mapping[str, object], base_url: str) -> RemoteIssue:
        """Compatibility seam that validates a raw Jira issue mapping."""
        return issue_to_remote(IssueWire.model_validate(raw), base_url)

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One issue, for the pre-push conflict check.

        Never cached: the entire purpose is to see what the tracker holds
        *right now*, and a cached answer would defeat it.
        """
        key = issue.key or issue.issue_id
        if not key:
            return None
        try:
            raw = self.api.issue(key, fields=ISSUE_FIELDS)
        except NotFoundError:
            return None
        return issue_to_remote(raw, self.required("base_url").rstrip("/"))

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield the complete visible Jira comment thread."""

        del project_id
        key = issue.key or issue.issue_id
        for comment in self.api.comments(key):
            yield comment_to_remote(comment, issue.issue_id)

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        comment: CommentDraft,
    ) -> RemoteComment:
        """Append a Jira comment as ADF text."""

        key = issue.key or issue.issue_id
        created = self.api.create_comment(key, comment_document(comment.body))
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        return comment_to_remote(created, issue.issue_id)

    # ---- writes ----------------------------------------------------------

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        """Transition an issue into the status behind ``column``.

        Jira does not let you set a status directly; you pick a transition that
        happens to end there. Which transitions exist depends on the workflow
        *and* on the issue's current status, so they are looked up per issue.
        """
        wanted = set(column.status_ids) or {column.column_id}
        transitions = self._transitions_for(issue.key)
        transition_id = next((tid for status_id, tid in transitions.items() if status_id in wanted), "")
        if not transition_id:
            available = ", ".join(sorted(transitions.values())) or "none"
            raise ProviderError(
                f"{issue.key} has no transition into {column.name}",
                hint=f"The workflow allows: {available}. It may need a field set first.",
            )
        self.api.transition(issue.key, transition_id)
        self._transitions.pop(issue.key, None)

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        """What this project accepts, from createmeta.

        Per project, not per site: two projects on the same Jira routinely
        offer different sets, which is why this takes a project id and is not
        a constant anywhere.

        Sub-tasks are reported with the flag rather than filtered out here, so
        a caller that genuinely wants one can still find it; everything that
        offers a choice for a new story hides them.
        """
        try:
            document = self.api.issue_types(project_id, ttl=TTL_STRUCTURE)
        except (NotFoundError, ProviderError):
            # An older Jira, or a project these credentials cannot create in.
            # Neither is worth failing a draft over: no list means "send no
            # type and let Jira decide".
            return []

        types: list[IssueType] = []
        for item in document.issueTypes or document.values:
            name = item.name
            if not name:
                continue
            types.append(
                IssueType(
                    type_id=item.id,
                    name=name,
                    subtask=item.subtask,
                    # -1 sub-task, 0 ordinary, 1 epic. Jira's own numbering
                    # already matches what IssueType.level means.
                    level=_as_int(item.hierarchyLevel),
                )
            )
        return types

    def list_components(self, project_id: str) -> list[IssueComponent]:
        """Return Jira components configured for this project.

        The REST response is paginated and the underlying client caches each
        page for six hours, matching other project-structure lookups.
        """
        try:
            records = self.api.components(project_id, ttl=TTL_STRUCTURE)
            return [
                IssueComponent(component_id=item.id, name=item.name, description=item.description)
                for item in records
                if item.id and item.name
            ]
        except (NotFoundError, ProviderError):
            return []

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        """Create an issue, then fetch it back.

        Two calls, and the second is not optional: ``POST /issue`` returns only
        ``id``, ``key`` and ``self``, so the file has to be written from a
        follow-up read. That read is also what catches a field Jira quietly
        dropped -- the created issue is the truth, not the request.

        Measured against a live project, only ``summary``, ``issuetype`` and
        ``project`` are required (``reporter`` defaults to the caller). Project
        configurations may require custom fields too; create metadata detects
        those before the POST when Jira exposes it, with Jira's create response
        remaining the compatibility fallback.
        """
        # By id, not key: ``project_id`` here is Jira's numeric project id (see
        # ``list_projects``), and sending that as a key gets the misleading
        # "the target project doesn't exist or you don't have permission".
        # Resolved against what the project actually offers. The old fallback
        # here was a literal "Task", which is not a type every project has --
        # and Jira reports an unknown type as a permission error on the
        # project, which sends you looking in entirely the wrong place.
        fields = self.build_create_payload(project_id, draft)
        issue_type = fields.get("issuetype")
        issue_type_id = (
            str(issue_type.get("id") or "") if isinstance(issue_type, dict) else ""
        )
        self._validate_create_fields(project_id, issue_type_id, fields)

        # api/2: the description round-trips as text rather than as ADF.
        created = self.api.create_issue(fields)
        key = created.key
        if not key:
            raise ProviderError("Jira accepted the issue but returned no key")

        raw = self.api.issue(key, fields=ISSUE_FIELDS)
        issue = issue_to_remote(raw, self.required("base_url").rstrip("/"))

        # A new issue lands in the workflow's first status, which may not be
        # the column it was drafted into. Move it if so, and report the
        # transition failing rather than leaving the file somewhere it is not.
        if draft.column_id and issue.column_id != draft.column_id:
            self.move_issue(issue, RemoteColumn(column_id=draft.column_id, name=draft.column_id))
            raw = self.api.issue(key, fields=ISSUE_FIELDS)
            issue = issue_to_remote(raw, self.required("base_url").rstrip("/"))
        return issue

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        issue_type = self.resolve_issue_type(project_id, draft.issue_type)
        return create_issue_fields(project_id, draft, issue_type)

    def _validate_create_fields(
        self,
        project_id: str,
        issue_type_id: str,
        fields: JsonObject,
    ) -> None:
        """Reject required create-screen fields the neutral draft cannot fill.

        Jira projects can make arbitrary custom fields mandatory. The generic
        issue draft intentionally does not accept arbitrary JSON, so discovering
        that gap before the POST prevents a partial or misleading create flow.
        """

        if not issue_type_id:
            return
        try:
            metadata = self.api.create_fields(
                project_id,
                issue_type_id,
                ttl=TTL_STRUCTURE,
            )
            missing = [
                item
                for item in metadata
                if item.required
                and "set" in item.operations
                and not item.hasDefaultValue
                and (item.fieldId or item.key) not in fields
                # Jira supplies the authenticated user when reporter is required.
                and (item.fieldId or item.key) != "reporter"
            ]
        except NotFoundError:
            # Preserve compatibility with Jira deployments that do not expose
            # the granular create-metadata route. Jira's create response still
            # names any required field it cannot populate.
            return

        if not missing:
            return
        labels = ", ".join(
            f"{item.name or item.fieldId} ({item.fieldId or item.key})" for item in missing
        )
        raise ProviderError(
            f"Jira requires create fields pykantui cannot supply: {labels}",
            hint=(
                "Give these fields defaults in the Jira field configuration, "
                "or create this issue in Jira. No issue was posted."
            ),
        )

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """Push a markdown edit back to Jira.

        Two requests at most, and deliberately in this order. Status is *not*
        a field on Jira -- it only moves through a transition -- so a column
        change is a separate call to the transitions endpoint. The field edit
        goes first: if the transition is the thing that fails (a workflow with
        a required field, say), the text edit has still landed, which is the
        less surprising half-outcome of the two.
        """
        self.reject_unsupported(edit)
        if "issue_type" in edit.cleared:
            raise ProviderError(
                "Jira issue type is required and cannot be cleared",
                hint="Choose an issue type such as Task, Story, or Bug.",
            )

        assignee_id = self._assignee_account_id(issue.key, edit.assignee) if edit.assignee is not None else None
        fields = update_issue_fields(edit, assignee_id=assignee_id)

        if fields:
            # api/2, so the description round-trips as text rather than ADF.
            self.api.update_issue(issue.key, fields)

        if edit.column_id:
            self.move_issue(issue, RemoteColumn(column_id=edit.column_id, name=edit.column_id))

    def _assignee_account_id(self, issue_key: str, query: str) -> str:
        """Resolve the human-facing editor value to Jira Cloud's account id."""
        candidates = self.api.assignable_users(issue_key, query)
        wanted = query.strip().casefold()
        exact = [
            user
            for user in candidates
            if wanted
            in {
                user.accountId.strip().casefold(),
                user.displayName.strip().casefold(),
                user.emailAddress.strip().casefold(),
            }
        ]
        matches = exact or (candidates if len(candidates) == 1 else [])
        account_ids = {
            user.accountId.strip() for user in matches if user.accountId.strip()
        }
        if len(account_ids) == 1:
            return account_ids.pop()
        if not account_ids:
            raise ProviderError(
                f'Jira could not find an assignable user matching "{query}"',
                hint="Use the person's exact display name, email, or Atlassian account ID.",
            )
        raise ProviderError(
            f'Jira found multiple assignable users matching "{query}"',
            hint="Use the person's email or Atlassian account ID to choose one.",
        )

    def _transitions_for(self, issue_key: str) -> dict[str, str]:
        """Map of destination status id -> transition id, cached per issue."""
        cached = self._transitions.get(issue_key)
        if cached is not None:
            return cached
        document = self.api.transitions(issue_key)
        found = {item.to.id: item.id for item in document.transitions}
        self._transitions[issue_key] = found
        return found

    def invalidate(self) -> None:
        self._transitions.clear()


def _group_for(name: str, category: str) -> ColumnGroup:
    """Column meaning from this tracker's own type, with the shared
    name heuristics either side of it. See tracker.columns."""
    return resolve_group(name, type_key=category, type_map=_CATEGORY_GROUPS)


def _as_int(value: object) -> int:
    try:
        return int(value) if isinstance(value, (str, int, float)) else 0
    except (TypeError, ValueError):
        return 0
