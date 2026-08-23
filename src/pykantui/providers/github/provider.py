"""GitHub Issues.

GitHub has no board of its own on the Issues API. Projects v2 is the real
kanban surface, and it is a separate GraphQL API with a different object model
(project items wrapping issues, columns as single-select field options). That
is worth doing and is not what this module does; a follow-up
``github_projects.py`` can register alongside this one without changing
anything here.

What this maps instead is how a large number of repositories actually work:

* **Status labels are the columns.** Give ``label_prefix`` a value -- the
  default is ``status:`` -- and every label starting with it becomes a column,
  so ``status:in progress`` is a column named "in progress".
* **Open and closed are the fallback**, for a repository with no such labels.
  Two columns, which is not much of a board but is an honest reading of what
  the repository contains.

Because a label is a column, moving a card is a label swap: the target label is
added and the other column labels are removed. That is why
:meth:`move_issue` is a read-modify-write rather than a single field set.

**Pull requests are excluded.** GitHub's issues endpoint returns PRs as issues
with a ``pull_request`` key, and a board full of them is nobody's intent.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import cast

from pykantui.api import TTL_STRUCTURE, JsonObject, ensure_json, page_by_number, parse_json
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.columns import group_from_name
from pykantui.tracker.errors import NotFoundError, UnsupportedError
from pykantui.tracker.models import (
    COLUMN_DONE,
    COLUMN_TODO,
    ColumnGroup,
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
from pykantui.tracker.spec import Capabilities, CredentialSetupKind, FieldKind, ProviderField, ProviderSpec

from .client import GitHubApi, GitHubClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import comment_to_remote, issue_to_remote, repository_to_remote, user_to_remote
from .payloads import create_comment_payload, create_issue_payload, update_issue_payload
from .schemas import CommentWire, IssueWire

API_URL = "https://api.github.com"

#: GitHub's REST API is versioned by header, and the version is pinned so a
#: server-side default moving cannot change our field shapes underneath us.
API_VERSION = "2026-03-10"

DEFAULT_LABEL_PREFIX = "status:"

#: Used when the repository has no status labels at all.
_OPEN_COLUMN = "state:open"
_CLOSED_COLUMN = "state:closed"


class GitHubProvider(Provider):
    spec = ProviderSpec(
        name="github",
        label="GitHub",
        description="GitHub Issues, with status labels as columns.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        token_url="https://github.com/settings/tokens",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="token",
                label="Access token",
                kind=FieldKind.SECRET,
                env_vars=("GITHUB_TOKEN", "GH_TOKEN"),
                help="A fine-grained or classic PAT with repo issue access.",
            ),
            ProviderField(
                name="base_url",
                label="API URL",
                kind=FieldKind.URL,
                required=False,
                default=API_URL,
                placeholder=API_URL,
                env_vars=("GITHUB_API_URL",),
                help="Change this only for GitHub Enterprise Server.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="repo",
                label="Repository",
                kind=FieldKind.CHOICE,
                placeholder="owner/name",
                env_vars=("GITHUB_REPOSITORY",),
            ),
            ProviderField(
                name="label_prefix",
                label="Status label prefix",
                required=False,
                default=DEFAULT_LABEL_PREFIX,
                placeholder=DEFAULT_LABEL_PREFIX,
                help="Labels starting with this become the board's columns.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,  # by swapping the status label
            reorder_issues=False,
            create_issues=True,
            read_comments=True,
            create_comments=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "issue_type"),
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
        verified=True,  # exercised against a live repository: read, create, edit, move and refresh
    )

    @property
    def http(self) -> GitHubClient:
        if self._http is None:
            self._http = GitHubClient.connect(
                self.optional("base_url", API_URL).rstrip("/"),
                self.required("token"),
                api_version=API_VERSION,
                cache=self.cache,
            )
        return cast(GitHubClient, self._http)

    @property
    def prefix(self) -> str:
        return self.optional("label_prefix", DEFAULT_LABEL_PREFIX)

    @property
    def api(self) -> GitHubApi:
        """Typed operations over the configured or injected transport."""
        return GitHubApi(self.http)

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        return user_to_remote(self.api.current_user())

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        """Repositories the token can see."""

        return [repository_to_remote(repository) for repository in page_by_number(self.api.repositories)]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        def fetch(page: int) -> list[str]:
            return self.api.labels(project_id, page)

        prefix = self.prefix
        columns: list[RemoteColumn] = []
        for name in page_by_number(fetch):
            if not prefix or not name.lower().startswith(prefix.lower()):
                continue
            display = name[len(prefix) :].strip() or name
            columns.append(
                RemoteColumn(
                    column_id=name,
                    name=display,
                    position=len(columns),
                    group=_group_for(display),
                    status_ids=(name,),
                )
            )

        if columns:
            return columns
        # No status labels: open and closed is the only board this repo has.
        return [
            RemoteColumn(column_id=_OPEN_COLUMN, name="Open", position=0, group=COLUMN_TODO),
            RemoteColumn(column_id=_CLOSED_COLUMN, name="Closed", position=1, group=COLUMN_DONE),
        ]

    # ---- issues ----------------------------------------------------------

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        prefix = self.prefix

        def fetch(page: int) -> list[IssueWire]:
            return self.api.issues(project_id, page)

        for issue in page_by_number(fetch):
            if issue.pull_request is not None:
                continue
            yield self._map_issue(issue, project_id, prefix)

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """Fetch one issue for the pre-push conflict check."""
        number = issue.extra.get("number")
        if number is None:
            return None
        try:
            raw = self.api.issue(project_id, number)
        except NotFoundError:
            return None
        if raw.pull_request is not None:
            return None
        return self._map_issue(raw, project_id, self.prefix)

    def _to_issue(self, raw: Mapping[str, object], repo: str, prefix: str) -> RemoteIssue:
        wire = parse_json(ensure_json(raw), IssueWire)
        return self._map_issue(wire, repo, prefix)

    @staticmethod
    def _map_issue(issue: IssueWire, repo: str, prefix: str) -> RemoteIssue:
        return issue_to_remote(
            issue,
            repo,
            prefix,
            open_column=_OPEN_COLUMN,
            closed_column=_CLOSED_COLUMN,
        )

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        """Issue types enabled for this repository by its organization owner."""
        try:
            document = self.api.issue_types(project_id, ttl=TTL_STRUCTURE)
        except NotFoundError:
            # Personal repositories do not inherit organization issue types.
            return []
        return [
            IssueType(type_id=str(item.id), name=item.name)
            for item in document.root
            if item.name
        ]

    def resolve_issue_type(self, project_id: str, wanted: str) -> IssueType | None:
        """Resolve only types GitHub says this repository accepts.

        GitHub may silently discard ``type`` when a repository has no types,
        so its absence must be treated as unsupported rather than as an
        invitation to send an unchecked value.
        """
        if not self.issue_types(project_id):
            if not wanted.strip():
                return None
            raise UnsupportedError(
                f"{self.spec.label} repository {project_id} has no issue types",
                hint="Leave Type empty; personal repositories do not inherit organization issue types.",
            )
        return super().resolve_issue_type(project_id, wanted)

    def editable_card_fields(self) -> tuple[str, ...]:
        """Expose Type only when this repository has real issue types."""
        return self._fields_supported_by_repository(super().editable_card_fields())

    def creatable_card_fields(self) -> tuple[str, ...]:
        """Expose Type on drafts only when GitHub will accept it."""
        return self._fields_supported_by_repository(super().creatable_card_fields())

    def reject_unsupported(self, edit: IssueEdit) -> None:
        """Avoid a capability request for edits unrelated to issue type."""
        declared = self.spec.editable_card_fields(self.config)
        if edit.unsupported(declared) or "issue_type" in edit.touched():
            super().reject_unsupported(edit)

    def _fields_supported_by_repository(self, fields: tuple[str, ...]) -> tuple[str, ...]:
        if self.issue_types(self.required("repo")):
            return fields
        return tuple(field for field in fields if field != "issue_type")

    # ---- comments --------------------------------------------------------

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield every issue-conversation comment in GitHub's chronological order."""
        number = issue.extra.get("number")
        if number is None:
            return

        def fetch(page: int) -> list[CommentWire]:
            return self.api.comments(project_id, number, page)

        for comment in page_by_number(fetch):
            yield comment_to_remote(comment, issue_id=issue.issue_id)

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        draft: CommentDraft,
    ) -> RemoteComment:
        """Append one Markdown comment to the GitHub issue conversation."""
        number = issue.extra.get("number")
        if number is None:
            raise NotFoundError("GitHub issue has no repository-local number")
        comment = self.api.create_comment(project_id, number, create_comment_payload(draft))
        created = comment_to_remote(comment, issue_id=issue.issue_id)
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        return created

    # ---- writes ----------------------------------------------------------

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        resolved_type: str | None = None
        if draft.issue_type:
            issue_type = self.resolve_issue_type(project_id, draft.issue_type)
            resolved_type = issue_type.name if issue_type else None
        return create_issue_payload(
            draft,
            resolved_type=resolved_type,
            open_column=_OPEN_COLUMN,
            closed_column=_CLOSED_COLUMN,
        )

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        raw = self.api.create_issue(project_id, self.build_create_payload(project_id, draft))
        if not raw.id:
            raise NotFoundError("GitHub accepted the issue but returned no id")
        return self._map_issue(raw, project_id, self.prefix)

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        """Swap the status label, or open/close where there are none.

        A read-modify-write: the issue's other status labels have to come off,
        and GitHub's label endpoints replace the whole set rather than
        patching it.
        """
        repo = self.required("repo")
        number = issue.extra.get("number")
        if number is None:
            return

        if column.column_id in (_OPEN_COLUMN, _CLOSED_COLUMN):
            state = "closed" if column.column_id == _CLOSED_COLUMN else "open"
            self.api.update_issue(repo, number, {"state": state})
            return

        prefix = self.prefix
        keep = [label for label in issue.labels if not (prefix and label.lower().startswith(prefix.lower()))]
        self.api.replace_labels(repo, number, [*keep, column.column_id])

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """One PATCH for the fields, including the label swap when the column moved.

        Labels go in the *same* PATCH as everything else, because a column is
        a label here -- sending them separately would leave a window in which
        the issue sits in no column at all.
        """
        self.reject_unsupported(edit)
        number = issue.extra.get("number")
        if number is None:
            return

        resolved_type: str | None = None
        if edit.issue_type is not None:
            issue_type = self.resolve_issue_type(self.required("repo"), edit.issue_type)
            resolved_type = issue_type.name if issue_type else None
        payload = update_issue_payload(
            issue,
            edit,
            prefix=self.prefix,
            resolved_type=resolved_type,
            open_column=_OPEN_COLUMN,
            closed_column=_CLOSED_COLUMN,
        )

        if payload:
            self.api.update_issue(self.required("repo"), number, payload)


def is_pull_request(raw: Mapping[str, object]) -> bool:
    """Whether an "issue" from GitHub's issues endpoint is really a PR.

    GitHub returns pull requests alongside issues and distinguishes them only
    by the presence of a ``pull_request`` key. Without this filter a board
    fills up with review traffic, which is nobody's intent.
    """
    return bool(raw.get("pull_request"))


def _group_for(name: str) -> ColumnGroup:
    """Column meaning from the name alone; this tracker types nothing."""
    return group_from_name(name)
