"""Linear.

**Unverified** -- written from the published API, no credentials to run it
against.

Linear is the best-fitting tracker of the set, because its workflow states
carry an explicit ``type`` from a fixed vocabulary: ``triage``, ``backlog``,
``unstarted``, ``started``, ``completed``, ``canceled``. Every other provider
here either guesses column meaning from a name (Trello, Monday) or has a
coarser grouping that loses the review column (Jira). Linear just tells us.

Two things to know:

* **The API key goes in ``Authorization`` with no scheme word** -- not
  ``Bearer``. OAuth access tokens *do* take ``Bearer``; personal API keys do
  not, and using the wrong one is the usual cause of a 401 here.
* **Teams are the board container**, not projects. A Linear "project" is a
  cross-team initiative and has no workflow states of its own, so
  :meth:`list_projects` returns teams -- which is what a board actually is.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

from pykantui.api import JsonObject, PayloadError
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_CANCELLED,
    COLUMN_DONE,
    COLUMN_REVIEW,
    COLUMN_STARTED,
    COLUMN_TODO,
    COLUMN_UNKNOWN,
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
from pykantui.tracker.util import float_or_none

from .client import LinearApi, LinearClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import comment_to_remote, issue_to_remote, team_to_remote, user_to_remote
from .payloads import create_issue_input, update_issue_input
from .schemas import IssueWire

API_URL = "https://api.linear.app/graphql"

#: Linear's own state types. The one fixed vocabulary in this package that maps
#: onto ours without guesswork.
_STATE_TYPES = {
    "triage": COLUMN_BACKLOG,
    "backlog": COLUMN_BACKLOG,
    "unstarted": COLUMN_TODO,
    "started": COLUMN_STARTED,
    "completed": COLUMN_DONE,
    "canceled": COLUMN_CANCELLED,
}

#: Only used to promote a review column out of ``started``, which is where
#: Linear puts it. Everything else comes from the type.
_REVIEW_NAMES = ("review", "qa", "testing")

def _is_uuid(value: str) -> bool:
    """Whether this already looks like Linear's own identifier.

    A shape test rather than a parse: the point is only to decide whether a
    lookup is needed, and anything that is not a UUID is treated as a label to
    resolve. A malformed UUID therefore reaches Linear and gets Linear's own
    error, which is more useful than one invented here.
    """
    parts = value.split("-")
    return len(parts) == 5 and all(
        len(part) == size and all(c in "0123456789abcdefABCDEF" for c in part)
        for part, size in zip(parts, (8, 4, 4, 4, 12), strict=True)
    )


class LinearProvider(Provider):
    spec = ProviderSpec(
        name="linear",
        label="Linear",
        verified=True,  # exercised against a live workspace: read, edit and push
        description="Linear teams, workflow states and issues.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        token_url="https://linear.app/settings/account/security",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="token",
                label="API key",
                kind=FieldKind.SECRET,
                env_vars=("LINEAR_TOKEN", "LINEAR_API_KEY"),
                help="Settings → API → Personal API keys. Starts with lin_api_.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="team_id",
                label="Team",
                kind=FieldKind.CHOICE,
                env_vars=("LINEAR_TEAM_ID",),
                help="Linear boards belong to a team, not to a project. Key or name is fine.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=True,  # sortOrder is a real per-issue rank
            create_issues=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "due_date", "priority"),
            read_comments=True,
            create_comments=True,
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    @property
    def http(self) -> LinearClient:
        if self._http is None:
            self._http = LinearClient.connect(
                self.optional("base_url", API_URL), self.required("token"), cache=self.cache
            )
        return cast(LinearClient, self._http)

    @property
    def api(self) -> LinearApi:
        """Return the typed Linear operation facade."""
        return LinearApi(self.http)

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        return user_to_remote(self.api.viewer())

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        """Teams, which is what a Linear board actually is."""
        return [team_to_remote(team) for team in self.api.teams()]

    def _team(self, project_id: str) -> str:
        """The team's UUID, from whatever the user had to hand.

        Linear's API identifies a team by UUID, and its web UI shows you the
        *key* ("ENG") and the *name* ("Engineering") -- the UUID appears nowhere a
        person would look. Demanding it is asking for something the product
        does not offer, so a key or a name is resolved here instead.

        Matching order is key, then name, both case-insensitively. Key first
        because it is unique by construction where two teams may share a name.
        """
        wanted = project_id.strip()
        if not wanted or _is_uuid(wanted):
            return wanted

        if wanted.casefold() not in self._teams_by_label:
            for team in self.list_projects():
                if team.key:
                    self._teams_by_label[team.key.casefold()] = team.project_id
                if team.name:
                    self._teams_by_label.setdefault(team.name.casefold(), team.project_id)

        found = self._teams_by_label.get(wanted.casefold())
        if found:
            return found

        known = ", ".join(sorted({k for k in self._teams_by_label})) or "none visible"
        raise ProviderError(
            f"Linear has no team called {project_id!r}",
            hint=f"Use the team key or name as shown in Linear. Found: {known}.",
        )

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)

        #: Team key and name to UUID, filled on first use. One lookup per run,
        #: not one per request.
        self._teams_by_label: dict[str, str] = {}

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One issue as Linear currently has it, for the conflict check.

        Without this a push reported "could not check for remote changes" on
        every card -- honest, but it meant a colleague's edit could be silently
        overwritten by yours. Linear resolves either the UUID or the human
        identifier here, so the id we stored is enough.
        """
        wanted = issue.issue_id or issue.key
        if not wanted:
            return None
        node = self.api.issue(wanted)
        return issue_to_remote(node) if node is not None else None

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        states = sorted(
            self.api.states(self._team(project_id)),
            key=lambda item: float_or_none(item.position) or 0.0,
        )
        return [
            RemoteColumn(
                column_id=state.id,
                name=state.name,
                position=position,
                group=_group_for(state.name, state.type),
                status_ids=(state.id,),
            )
            for position, state in enumerate(states)
        ]

    # ---- issues ----------------------------------------------------------

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        for node in self.api.issues(self._team(project_id)):
            yield issue_to_remote(node)

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield every Linear comment without per-comment lookups."""

        del project_id
        for comment in self.api.comments(issue.issue_id or issue.key):
            yield comment_to_remote(comment, issue.issue_id)

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        comment: CommentDraft,
    ) -> RemoteComment:
        """Append one Markdown comment through commentCreate."""

        created = self.api.create_comment(issue.issue_id or issue.key, comment.body)
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        if created is None or not created.id:
            raise PayloadError("Linear accepted the comment but returned no canonical comment")
        return comment_to_remote(created, issue.issue_id)

    def _to_issue(self, node: Mapping[str, object]) -> RemoteIssue:
        """Compatibility mapper used by provider-level mapping tests."""
        return issue_to_remote(IssueWire.model_validate(node))

    # ---- writes ----------------------------------------------------------

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        return create_issue_input(
            draft,
            project_id,
            label_ids=self._resolve_label_ids(draft.labels) if draft.labels else [],
        )

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        node = self.api.create_issue(self.build_create_payload(project_id, draft))
        if node is None or not node.id:
            raise ProviderError("Linear accepted the issue but returned no id")
        return issue_to_remote(node)

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self.api.move_issue(issue.issue_id, column.column_id)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """One issueUpdate mutation carries every field Linear accepts."""
        self.reject_unsupported(edit)
        payload = update_issue_input(
            edit,
            assignee_id=self._resolve_user_id(edit.assignee)
            if edit.assignee is not None
            else None,
            label_ids=self._resolve_label_ids(edit.labels)
            if edit.labels is not None
            else None,
        )
        if payload:
            self.api.update_issue(issue.issue_id, payload)

    def _resolve_user_id(self, value: str) -> str:
        users = [user.model_dump() for user in self.api.users()]
        return resolve_ids(users, value, name_keys=("name", "displayName", "email"), field_label="Linear user")[0]

    def _resolve_label_ids(self, values: tuple[str, ...]) -> list[str]:
        labels = [label.model_dump() for label in self.api.labels()]
        return resolve_ids(labels, values, name_keys=("name",), field_label="Linear label")


def _group_for(name: str, state_type: str) -> str:
    """Linear's state type, with review promoted out of ``started``.

    Linear has no review type -- a "In Review" state is just another
    ``started`` -- so the name is consulted for that one distinction and
    nothing else.
    """
    lowered = name.strip().lower()
    if any(needle in lowered for needle in _REVIEW_NAMES):
        return COLUMN_REVIEW
    return _STATE_TYPES.get(state_type.strip().lower(), COLUMN_UNKNOWN)
