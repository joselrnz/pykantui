"""ClickUp.

**Unverified** -- written from the published API, no credentials to run it
against.

ClickUp fits the board model well: a **List** owns an ordered set of
**statuses**, and each status carries a ``type`` from a small fixed vocabulary
(``open``, ``custom``, ``closed``, ``done``). So columns come with their
meaning attached, and only the ``custom`` ones need their name inspected.

The awkward part is finding a list in the first place. ClickUp nests them four
deep -- team → space → folder → list -- and lists can hang directly off a space
without a folder. :meth:`list_projects` walks all of that, which is several
round trips; it is a wizard-time operation, not something the sync repeats.

Auth is the token bare in ``Authorization``, with no ``Bearer``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

from pykantui.api import TTL_STRUCTURE, JsonObject, PaginationError, page_by_number
from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker.base import Provider
from pykantui.tracker.columns import resolve_group
from pykantui.tracker.errors import NotFoundError, ProviderError
from pykantui.tracker.models import (
    COLUMN_DONE,
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

from .client import ClickUpApi, ClickUpClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import comment_to_remote, epoch_to_iso, list_to_remote, task_to_remote, user_to_remote
from .payloads import create_comment_payload, create_task_payload, update_task_payload
from .schemas import ListWire, TaskWire

API_URL = "https://api.clickup.com/api/v2"

#: ClickUp's status types. ``custom`` says nothing, so those fall through to
#: the name.
_STATUS_TYPES = {
    "open": COLUMN_TODO,
    "closed": COLUMN_DONE,
    "done": COLUMN_DONE,
}

_COMMENT_PAGE_SIZE = 25
_COMMENT_MAX_PAGES = 1_000


class ClickUpProvider(Provider):
    spec = ProviderSpec(
        name="clickup",
        label="ClickUp",
        verified=True,  # read, edit and push against a live workspace
        description="ClickUp lists, statuses and tasks.",
        table_fields=(WorkItemColumn.REPORTER, WorkItemColumn.CREATED),
        token_url="https://app.clickup.com/settings/apps",
        credential_setup=CredentialSetupKind.PERSONAL,
        auth_fields=(
            ProviderField(
                name="token",
                label="API token",
                kind=FieldKind.SECRET,
                env_vars=("CLICKUP_TOKEN", "CLICKUP_API_TOKEN"),
                help="Settings → Apps → API token. Starts with pk_.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="list_id",
                label="List",
                kind=FieldKind.CHOICE,
                env_vars=("CLICKUP_LIST_ID",),
                help="A ClickUp list is the board.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=True,  # orderindex is a real per-task rank
            create_issues=True,
            read_comments=True,
            create_comments=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "due_date", "priority", "issue_type"),
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)
        self._type_names_by_team: dict[str, dict[str, str]] = {}

    @property
    def http(self) -> ClickUpClient:
        if self._http is None:
            self._http = ClickUpClient.connect(
                self.optional("base_url", API_URL), self.required("token"), cache=self.cache
            )
        return cast(ClickUpClient, self._http)

    @property
    def api(self) -> ClickUpApi:
        """Typed operations over the configured or injected transport."""
        return ClickUpApi(self.http)

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        return user_to_remote(self.api.current_user())

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        """Every list the token can reach, walked team → space → folder → list.

        Several round trips, deliberately: this runs once in the wizard so the
        user can pick a board, not on every sync.
        """
        found: list[RemoteProject] = []
        for team in self.api.teams():
            for space in self.api.spaces(str(team.id)):
                found.extend(self._lists_in_space(str(space.id), space.name))
        return found

    def _lists_in_space(self, space_id: str, space_name: str) -> list[RemoteProject]:
        found: list[RemoteProject] = []

        # Lists can sit directly in a space, with no folder in between.
        found.extend(list_to_remote(entry, space_name) for entry in self.api.space_lists(space_id))
        for folder in self.api.folders(space_id).folders:
            label = f"{space_name}/{folder.name}"
            found.extend(list_to_remote(entry, label) for entry in folder.lists)
        return found

    @staticmethod
    def _as_project(entry: Mapping[str, object], where: str) -> RemoteProject:
        """Compatibility seam that validates a raw list mapping."""
        return list_to_remote(ListWire.model_validate(entry), where)

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        document = self._list(project_id)
        statuses = sorted(
            document.statuses,
            key=lambda item: sort_key(item.orderindex),
        )
        return [
            RemoteColumn(
                # ClickUp identifies a task's status by its *name*, not its id.
                column_id=status.status,
                name=status.status,
                position=position,
                group=_group_for(status.status, status.type),
                status_ids=(status.status,),
            )
            for position, status in enumerate(statuses)
        ]

    # ---- issues ----------------------------------------------------------

    def _list(self, list_id: str) -> ListWire:
        """Fetch the list, or explain what was configured instead of one.

        ClickUp nests Workspace > Space > Folder > List, and only the *list* is
        a board. Give it any of the others and it answers 401 "Team not
        authorized" -- which reads as a credentials problem and sends you to
        check your token, when the token is fine and the id is simply the wrong
        kind of object. Measured: a workspace id produced exactly that.
        """
        try:
            return self.api.list_(list_id)
        except (ProviderError, NotFoundError) as error:
            kind = self._what_is(list_id)
            if kind:
                raise ProviderError(
                    f"ClickUp {list_id} is a {kind}, not a list",
                    hint=("A ClickUp *list* is the board. Run `kbn init --type clickup --list-ids` to see the lists."),
                ) from error
            raise

    def _what_is(self, wanted: str) -> str:
        """Whether this id is a workspace or a space, for a better error.

        Best effort: a lookup that fails leaves the original error alone rather
        than replacing a real problem with a guess about the id.
        """
        try:
            teams = self.api.teams()
        except ProviderError:
            return ""
        for team in teams:
            if str(team.id) == wanted:
                return "workspace"
            try:
                spaces = self.api.spaces(str(team.id))
            except ProviderError:
                continue
            if any(str(space.id) == wanted for space in spaces):
                return "space"
        return ""

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        def fetch(page: int) -> list[TaskWire]:
            return self.api.tasks(project_id, page)

        # ClickUp pages from zero and returns a fixed 100 per page.
        for task in page_by_number(fetch, first_page=0, page_size=100):
            yield task_to_remote(task, self._type_names(str(task.team_id)))

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One issue as the tracker has it now, for the conflict check.

        Re-read through the same mapping the listing uses: comparing a
        differently-populated copy of an issue against its snapshot invents
        changes that are not there.
        """
        if not issue.issue_id:
            return None
        try:
            task = self.api.task(issue.issue_id)
        except NotFoundError:
            return None
        return task_to_remote(task, self._type_names(str(task.team_id)))

    def _to_issue(
        self,
        task: Mapping[str, object],
        type_names: Mapping[str, str] | None = None,
    ) -> RemoteIssue:
        """Compatibility seam that validates a raw task mapping."""
        return task_to_remote(TaskWire.model_validate(task), type_names)

    def _type_names(self, team_id: str) -> dict[str, str]:
        """Return one cached workspace type directory, including built-ins."""
        if not team_id:
            return {"0": "Task", "1": "Milestone"}
        cached = self._type_names_by_team.get(team_id)
        if cached is not None:
            return cached
        names = {"0": "Task", "1": "Milestone"}
        try:
            records = self.api.custom_item_types(team_id, ttl=TTL_STRUCTURE)
        except ProviderError:
            records = []
        names.update(
            (str(item.id), item.name)
            for item in records
            if str(item.id) and item.name
        )
        self._type_names_by_team[team_id] = names
        return names

    # ---- comments --------------------------------------------------------

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield every task comment oldest first using ClickUp's compound cursor."""
        del project_id
        if not issue.issue_id:
            return
        start: str | None = None
        start_id: str | None = None
        seen: set[tuple[str, str]] = set()
        seen_ids: set[str] = set()
        newest_first: list[RemoteComment] = []
        for _ in range(_COMMENT_MAX_PAGES):
            page = self.api.comments(
                issue.issue_id,
                start=start,
                start_id=start_id,
            )
            for comment in page:
                comment_id = str(comment.id)
                if comment_id in seen_ids:
                    continue
                seen_ids.add(comment_id)
                newest_first.append(
                    comment_to_remote(comment, issue_id=issue.issue_id, issue_url=issue.url)
                )
                if comment.reply_count:
                    for reply in self.api.comment_replies(comment_id):
                        reply_id = str(reply.id)
                        if reply_id in seen_ids:
                            continue
                        seen_ids.add(reply_id)
                        newest_first.append(
                            comment_to_remote(
                                reply,
                                issue_id=issue.issue_id,
                                issue_url=issue.url,
                                parent_id=comment_id,
                            )
                        )
            if len(page) < _COMMENT_PAGE_SIZE:
                break
            last = page[-1]
            marker = (str(last.date), str(last.id))
            if marker in seen:
                raise PaginationError(
                    "ClickUp returned a repeated cursor; comment sync is incomplete",
                    hint="No comments were pruned. Retry after ClickUp pagination recovers.",
                )
            seen.add(marker)
            start, start_id = marker
        else:
            raise PaginationError(
                f"ClickUp comment pagination reached the {_COMMENT_MAX_PAGES}-page safety limit",
                hint="No comments were pruned. Narrow the card discussion and retry.",
            )
        yield from sorted(
            newest_first,
            key=lambda comment: (
                comment.created_at is None,
                comment.created_at.isoformat() if comment.created_at is not None else "",
                comment.comment_id,
            ),
        )

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        draft: CommentDraft,
    ) -> RemoteComment:
        """Create a task comment, then read its canonical author and body back."""
        if not issue.issue_id:
            raise NotFoundError("ClickUp task has no id")
        created = self.api.create_comment(issue.issue_id, create_comment_payload(draft))
        wanted_id = str(created.id)
        self.invalidate_comment_cache(project_id, issue.issue_id)
        for comment in self.comments(project_id, issue, refresh=True):
            if comment.comment_id == wanted_id:
                return comment
        raise NotFoundError(
            f"ClickUp accepted comment {wanted_id} but did not return it on read-back",
            hint="Refresh the card before adding another comment; the first write may have succeeded.",
        )

    # ---- writes ----------------------------------------------------------

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        return create_task_payload(draft)

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        task = self.api.create_task(project_id, self.build_create_payload(project_id, draft))
        if not task.id:
            raise NotFoundError("ClickUp accepted the task but returned no id")
        return task_to_remote(task, self._type_names(str(task.team_id)))

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self.api.update_task(issue.issue_id, {"status": column.column_id})

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """One PUT. ClickUp wants a due date back as epoch milliseconds."""
        self.reject_unsupported(edit)
        assignee_ids = self._resolve_assignee_ids(edit.assignee) if edit.assignee is not None else None
        payload = update_task_payload(issue, edit, assignee_ids=assignee_ids)
        if payload:
            self.api.update_task(issue.issue_id, payload)
        if edit.labels is not None:
            wanted = set(edit.labels)
            for label in sorted(set(issue.labels) - wanted):
                self.api.remove_tag(issue.issue_id, label)
            for label in sorted(wanted - set(issue.labels)):
                self.api.add_tag(issue.issue_id, label)
        elif "labels" in edit.cleared:
            for label in issue.labels:
                self.api.remove_tag(issue.issue_id, label)

    def _resolve_assignee_ids(self, value: str) -> list[int]:
        members = [member.model_dump() for member in self.api.members(self.required("list_id"))]
        resolved = resolve_ids(
            members,
            value,
            name_keys=("username", "email"),
            field_label="ClickUp user",
        )
        return [int(item) for item in resolved]


def _epoch(value: Any) -> Any:
    """Compatibility alias for ClickUp epoch-millisecond conversion."""
    return epoch_to_iso(value)


def _group_for(name: str, status_type: str) -> str:
    """Column meaning from this tracker's own type, with the shared
    name heuristics either side of it. See tracker.columns."""
    return resolve_group(name, type_key=status_type, type_map=_STATUS_TYPES)
