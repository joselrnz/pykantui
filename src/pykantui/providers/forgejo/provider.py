"""Forgejo Issues, with status labels as board columns."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import cast

from pykantui.api import JsonObject, ensure_json, page_by_number, parse_json
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.columns import group_from_name
from pykantui.tracker.errors import NotFoundError
from pykantui.tracker.models import (
    COLUMN_DONE,
    COLUMN_GROUPS,
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
from pykantui.tracker.spec import Capabilities, CredentialSetupKind, FieldKind, ProviderField, ProviderSpec

from .client import ForgejoApi, ForgejoClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import comment_to_remote, issue_to_remote, repository_to_remote, user_to_remote
from .payloads import create_comment_payload, create_issue_payload, update_issue_payload
from .schemas import IssueWire, LabelWire

DEFAULT_LABEL_PREFIX = "status:"
_OPEN_COLUMN = "state:open"
_CLOSED_COLUMN = "state:closed"
_PAGE_SIZE = 50


class ForgejoProvider(Provider):
    spec = ProviderSpec(
        name="forgejo",
        label="Forgejo",
        description="Self-hosted Forgejo Issues, with status labels as columns.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        token_url="https://forgejo.org/docs/latest/user/api/usage/#generating-and-listing-api-tokens",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="base_url",
                label="Forgejo URL",
                kind=FieldKind.URL,
                placeholder="https://forge.example.com",
                env_vars=("FORGEJO_URL", "FORGEJO_BASE_URL"),
                help="The HTTPS instance URL; /api/v1 is added automatically.",
            ),
            ProviderField(
                name="token",
                label="Access token",
                kind=FieldKind.SECRET,
                env_vars=("FORGEJO_TOKEN",),
                help="A personal token with read:user, read:repository, and write:issue access.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="repo",
                label="Repository",
                kind=FieldKind.CHOICE,
                placeholder="owner/name",
                env_vars=("FORGEJO_REPOSITORY",),
            ),
            ProviderField(
                name="label_prefix",
                label="Status label prefix",
                required=False,
                default=DEFAULT_LABEL_PREFIX,
                placeholder=DEFAULT_LABEL_PREFIX,
                env_vars=("FORGEJO_LABEL_PREFIX",),
                help="Labels starting with this become the board's columns.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            create_issues=True,
            read_comments=True,
            create_comments=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "due_date"),
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
        verified=True,  # exercised live: discover, create, edit, comment, close, read back and clean up
    )

    def __init__(self, config: Mapping[str, object], secrets: Mapping[str, str]) -> None:
        super().__init__(config, secrets)
        self._label_directory: dict[str, list[LabelWire]] = {}

    @property
    def http(self) -> ForgejoClient:
        if self._http is None:
            self._http = ForgejoClient.connect(
                self.required("base_url"),
                self.required("token"),
                cache=self.cache,
            )
        return cast(ForgejoClient, self._http)

    @property
    def api(self) -> ForgejoApi:
        return ForgejoApi(self.http)

    @property
    def prefix(self) -> str:
        return self.optional("label_prefix", DEFAULT_LABEL_PREFIX)

    def verify(self) -> RemoteUser:
        return user_to_remote(self.api.current_user())

    def list_projects(self) -> list[RemoteProject]:
        repositories = page_by_number(self.api.repositories, page_size=_PAGE_SIZE)
        return [repository_to_remote(repository) for repository in repositories if repository.has_issues]

    def _labels(self, project_id: str) -> list[LabelWire]:
        cached = self._label_directory.get(project_id)
        if cached is None:
            cached = list(page_by_number(lambda page: self.api.labels(project_id, page), page_size=_PAGE_SIZE))
            self._label_directory[project_id] = cached
        return cached

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        prefix = self.prefix
        columns: list[RemoteColumn] = []
        for label in self._labels(project_id):
            if not prefix or not label.name.casefold().startswith(prefix.casefold()):
                continue
            display = label.name[len(prefix) :].strip() or label.name
            columns.append(
                RemoteColumn(
                    column_id=label.name,
                    name=display,
                    position=len(columns),
                    group=group_from_name(display),
                    status_ids=(label.name,),
                )
            )
        if columns:
            group_order = {group: position for position, group in enumerate(COLUMN_GROUPS)}
            columns.sort(key=lambda column: (group_order[column.group], column.name.casefold()))
            return [column.model_copy(update={"position": position}) for position, column in enumerate(columns)]
        return [
            RemoteColumn(column_id=_OPEN_COLUMN, name="Open", position=0, group=COLUMN_TODO),
            RemoteColumn(column_id=_CLOSED_COLUMN, name="Closed", position=1, group=COLUMN_DONE),
        ]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        for issue in page_by_number(
            lambda page: self.api.issues(project_id, page),
            page_size=_PAGE_SIZE,
        ):
            if issue.pull_request is None:
                yield self._map_issue(issue, project_id, self.prefix)

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
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

    def _to_issue(self, raw: Mapping[str, object], repository: str, prefix: str) -> RemoteIssue:
        return self._map_issue(parse_json(ensure_json(raw), IssueWire), repository, prefix)

    @staticmethod
    def _map_issue(issue: IssueWire, repository: str, prefix: str) -> RemoteIssue:
        return issue_to_remote(
            issue,
            repository,
            prefix,
            open_column=_OPEN_COLUMN,
            closed_column=_CLOSED_COLUMN,
        )

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        number = issue.extra.get("number")
        if number is None:
            return
        for comment in page_by_number(
            lambda page: self.api.comments(project_id, number, page),
            page_size=_PAGE_SIZE,
        ):
            yield comment_to_remote(comment, issue_id=issue.issue_id)

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        draft: CommentDraft,
    ) -> RemoteComment:
        number = issue.extra.get("number")
        if number is None:
            raise NotFoundError("Forgejo issue has no repository-local number")
        raw = self.api.create_comment(project_id, number, create_comment_payload(draft))
        created = comment_to_remote(raw, issue_id=issue.issue_id)
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        return created

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        names = list(draft.labels)
        if draft.column_id not in ("", _OPEN_COLUMN, _CLOSED_COLUMN):
            names.append(draft.column_id)
        label_ids = self._resolve_label_ids(project_id, names)
        return create_issue_payload(draft, label_ids=label_ids, closed_column=_CLOSED_COLUMN)

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        raw = self.api.create_issue(project_id, self.build_create_payload(project_id, draft))
        if not raw.id:
            raise NotFoundError("Forgejo accepted the issue but returned no id")
        return self._map_issue(raw, project_id, self.prefix)

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self.update_issue(issue, IssueEdit(column_id=column.column_id))

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)
        number = issue.extra.get("number")
        if number is None:
            return
        repository = self.required("repo")
        payload = update_issue_payload(edit, open_column=_OPEN_COLUMN, closed_column=_CLOSED_COLUMN)
        if payload:
            self.api.update_issue(repository, number, payload)

        labels_touched = edit.labels is not None or "labels" in edit.cleared
        column_touched = edit.column_id is not None
        if labels_touched or column_touched:
            labels = [] if "labels" in edit.cleared else list(edit.labels if edit.labels is not None else issue.labels)
            column = edit.column_id if edit.column_id is not None else issue.column_id
            labels = [name for name in labels if not self._is_status_label(name)]
            if column not in ("", _OPEN_COLUMN, _CLOSED_COLUMN):
                labels.append(column)
            self.api.replace_labels(repository, number, list(dict.fromkeys(labels)))

    def _resolve_label_ids(self, project_id: str, names: list[str]) -> list[int]:
        if not names:
            return []
        wanted = {name.casefold(): name for name in names if name.strip()}
        found = {label.name.casefold(): label.id for label in self._labels(project_id)}
        missing = [original for folded, original in wanted.items() if folded not in found]
        if missing:
            raise NotFoundError(
                f"Forgejo repository {project_id} has no label named {missing[0]!r}",
                hint="Create the label in Forgejo first, then retry the sync.",
            )
        try:
            return [int(found[name.casefold()]) for name in names if name.strip()]
        except (TypeError, ValueError) as error:
            raise NotFoundError("Forgejo returned a label without a numeric id") from error

    def _is_status_label(self, name: str) -> bool:
        return bool(self.prefix and name.casefold().startswith(self.prefix.casefold()))


__all__ = ["ForgejoProvider"]
