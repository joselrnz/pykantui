"""Trello.

**Getting credentials changed, and the old instructions are dead.**
``trello.com/app-key`` now answers 401: an API key is tied to a *Power-Up*, so
one has to exist before a key can be issued. Verified against Atlassian's
current REST guide:

1. Create a Power-Up at https://trello.com/apps/admin (name and workspace are
   enough; the iframe URL matters only for a Power-Up people actually install).
2. Open it, go to the **API Key** tab, generate a key.
3. Click the **Token** link beside the key, approve, and copy the token.

The token step is a manual authorisation flow rather than OAuth, so the URL
matters: ``scope=read,write`` because this provider moves and edits cards, and
``expiration=never`` so a board stops syncing on a schedule nobody remembers
setting. :func:`token_url_for` builds it.

The response boundary is exercised against a live board. In particular,
Trello returns ``email: null`` for accounts that do not expose an address even
when that field is explicitly requested; the wire model accepts that shape
while the provider-neutral user model keeps an empty string.

Trello differs from the other two in ways worth knowing up front:

* Auth is two query parameters, ``key`` and ``token``, not a header. The key
  identifies the application; the token identifies the user who authorised it.
* Lists *are* the columns, and they carry no status semantics at all -- no
  category, no state group, nothing. Column meaning has to be guessed from the
  name -- :mod:`pykantui.tracker.columns` does that guessing for every
  tracker that needs it -- and the guess is allowed to come back ``unknown``.
* Card bodies (``desc``) are already markdown, so no conversion is needed.
* There is no paging on the list endpoints used here; a board returns all its
  cards in one response.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
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

from .client import TrelloApi, TrelloClient
from .fields import CARD_FIELDS, FILTER_LABELS
from .mapper import action_to_remote, board_to_remote, card_to_remote, member_to_remote
from .payloads import create_card_params, update_card_params
from .schemas import CardWire, MemberWire

API_ROOT = "https://api.trello.com/1"


def token_url_for(key: str) -> str:
    """The authorisation URL that hands back a token for your own account.

    ``read,write`` because this provider moves cards, and ``never`` because the
    documented example issues a one-day token -- which works perfectly today
    and silently stops working tomorrow.
    """
    return (
        f"https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&name=pykantui&key={key}"
    )


#: Every field a card needs. Shared by the board listing and the single-card
#: fetch: asking for different sets would make an unchanged card look edited.
_CARD_FIELDS = "id,idShort,name,desc,idList,due,dueComplete,pos,url,labels,dateLastActivity,closed,idMembers"


class TrelloProvider(Provider):
    spec = ProviderSpec(
        name="trello",
        label="Trello",
        verified=True,  # read, edit and push against a live board
        description="Trello boards, lists and cards.",
        table_fields=(WorkItemColumn.REPORTER,),
        token_url="https://trello.com/apps/admin",
        credential_setup=CredentialSetupKind.PROVIDER_APPLICATION,
        auth_fields=(
            ProviderField(
                name="key",
                label="API key",
                kind=FieldKind.SECRET,
                env_vars=("TRELLO_KEY", "TRELLO_API_KEY"),
                help="Power-Up Admin Portal -> your Power-Up -> API Key tab. Public, not secret.",
            ),
            ProviderField(
                name="token",
                label="API token",
                kind=FieldKind.SECRET,
                env_vars=("TRELLO_TOKEN",),
                help="The 'Token' link beside the API key. Grants full account access.",
            ),
        ),
        config_fields=(
            ProviderField(
                name="board_id",
                label="Board",
                kind=FieldKind.CHOICE,
                env_vars=("TRELLO_BOARD_ID",),
                help="Trello identifies boards by a 24-character id.",
            ),
        ),
        capabilities=Capabilities(
            move_issues=True,
            reorder_issues=True,  # cards carry a float `pos`
            create_issues=True,
            writable_fields=("title", "body", "column_id", "assignee", "labels", "due_date"),
            read_comments=True,
            create_comments=True,
        ),
        card_fields=CARD_FIELDS,
        filter_labels=FILTER_LABELS,
    )

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)
        self._members_by_board: dict[str, tuple[MemberWire, ...]] = {}

    @property
    def http(self) -> TrelloClient:
        if self._http is None:
            self._http = TrelloClient.connect(
                self.optional("base_url", API_ROOT),
                self.required("key"),
                self.required("token"),
                cache=self.cache,
            )
        return cast(TrelloClient, self._http)

    @property
    def api(self) -> TrelloApi:
        """Typed operations over the configured or injected transport."""
        return TrelloApi(self.http, self.required("key"), self.required("token"))

    # ---- connection ------------------------------------------------------

    def verify(self) -> RemoteUser:
        return member_to_remote(self.api.current_member())

    # ---- projects and columns -------------------------------------------

    def list_projects(self) -> list[RemoteProject]:
        """Trello boards, presented as projects."""
        return [board_to_remote(board) for board in self.api.boards() if not board.closed]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        ordered = sorted(self.api.lists(project_id), key=lambda item: sort_key(item.pos))
        return [
            RemoteColumn(
                column_id=item.id,
                name=item.name,
                position=position,
                group=_group_for(item.name),
                status_ids=(item.id,),
            )
            for position, item in enumerate(ordered)
        ]

    # ---- issues ----------------------------------------------------------

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        names = {column.column_id: column.name for column in self.columns(project_id)}
        members = self._member_names(project_id)
        for card in self.api.cards(project_id, fields=_CARD_FIELDS):
            if card.closed:
                continue
            yield card_to_remote(card, names, members)

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """One card as Trello has it now, for the conflict check.

        The same field selection as the board listing, so the comparison is
        between like and like -- a card fetched with fewer fields would look
        edited in every field the second request happened not to ask for.
        """
        if not issue.issue_id:
            return None
        try:
            card = self.api.card(issue.issue_id, fields=_CARD_FIELDS)
        except NotFoundError:
            return None
        names = {column.column_id: column.name for column in self.columns(project_id)}
        return card_to_remote(card, names, self._member_names(project_id))

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield only Trello commentCard actions, in stable chronological order."""

        del project_id
        comments = [
            action_to_remote(action, issue.issue_id)
            for action in self.api.comments(issue.issue_id)
            if action.type == "commentCard"
        ]
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
        """Append a Trello comment action."""

        created = self.api.create_comment(issue.issue_id, comment.body)
        self.invalidate_comment_cache(project_id, issue.issue_id or issue.key)
        return action_to_remote(created, issue.issue_id)

    def _to_issue(
        self,
        card: Mapping[str, object],
        names: dict[str, str],
        members: Mapping[str, str] | None = None,
    ) -> RemoteIssue:
        """Compatibility seam that validates raw card mappings."""
        return card_to_remote(CardWire.model_validate(card), names, members)

    # ---- writes ----------------------------------------------------------

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> JsonObject:
        label_ids = self._resolve_label_ids(draft.labels) if draft.labels else []
        return dict(create_card_params(draft, label_ids=label_ids))

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        card = self.api.create_card(create_card_params(draft, label_ids=self._resolve_label_ids(draft.labels)))
        if not card.id:
            raise NotFoundError("Trello accepted the card but returned no id")
        names = {column.column_id: column.name for column in self.columns(project_id)}
        return card_to_remote(card, names, self._member_names(project_id))

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        self.api.update_card(issue.issue_id, {"idList": column.column_id})

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """Trello takes the whole edit as query parameters on one PUT."""
        self.reject_unsupported(edit)
        member_ids = self._resolve_member_ids(edit.assignee) if edit.assignee is not None else None
        label_ids = self._resolve_label_ids(edit.labels) if edit.labels is not None else None
        params = update_card_params(edit, member_ids=member_ids, label_ids=label_ids)
        if params:
            self.api.update_card(issue.issue_id, params)

    def _resolve_member_ids(self, value: str) -> list[str]:
        members = [member.model_dump() for member in self._members(self.required("board_id"))]
        return resolve_ids(
            members,
            value,
            name_keys=("fullName", "username"),
            field_label="Trello member",
        )

    def _members(self, board_id: str) -> tuple[MemberWire, ...]:
        """Return the board member directory once per provider instance."""
        cached = self._members_by_board.get(board_id)
        if cached is None:
            cached = tuple(self.api.members(board_id))
            self._members_by_board[board_id] = cached
        return cached

    def _member_names(self, board_id: str) -> dict[str, str]:
        """Map known member ids to the names visible in Trello."""
        return {
            member.id: member.fullName or member.username
            for member in self._members(board_id)
            if member.id and (member.fullName or member.username)
        }

    def _resolve_label_ids(self, values: tuple[str, ...]) -> list[str]:
        labels = [label.model_dump() for label in self.api.labels(self.required("board_id"))]
        return resolve_ids(
            labels,
            values,
            name_keys=("name", "color"),
            field_label="Trello label",
        )


def _group_for(name: str) -> str:
    """Column meaning from the name alone; this tracker types nothing."""
    return group_from_name(name)
