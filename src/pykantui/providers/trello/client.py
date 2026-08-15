"""Authenticated Trello API client."""

from collections.abc import Iterator
from typing import Any, Self

from pykantui.api import JsonClient, JsonHttp, PaginationError, QueryParams, ResponseCache, parse_json

from . import routes
from .schemas import (
    ActionsWire,
    BoardsWire,
    BoardWire,
    CardsWire,
    CardWire,
    CommentActionWire,
    LabelsWire,
    LabelWire,
    ListsWire,
    ListWire,
    MembersWire,
    MemberWire,
)

_CREATION_ACTIONS = "createCard,copyCard"


def _card_read_params(fields: str) -> dict[str, Any]:
    """Fields that keep assignee and creator data in the bulk card read."""
    return {
        "fields": fields,
        "actions": _CREATION_ACTIONS,
        "actions_limit": 1,
        "action_fields": "type,idMemberCreator",
        "action_memberCreator": "true",
        "action_memberCreator_fields": "id,fullName,username",
    }


class TrelloClient(JsonHttp):
    """Trello carries its key and token in every request's query string."""

    def __init__(
        self,
        base_url: str,
        key: str,
        token: str,
        *,
        cache: ResponseCache | None = None,
    ) -> None:
        super().__init__(base_url.rstrip("/"), cache=cache, sensitive_values=(key, token))
        self.__key = key
        self.__token = token

    @classmethod
    def connect(
        cls,
        base_url: str,
        key: str,
        token: str,
        *,
        cache: ResponseCache | None = None,
    ) -> Self:
        return cls(base_url, key, token, cache=cache)

    def auth_params(self, **extra: Any) -> dict[str, Any]:
        """Return credentials plus provider-specific query parameters."""
        return self.auth_params_for(self.__key, self.__token, **extra)

    @staticmethod
    def auth_params_for(key: str, token: str, **extra: Any) -> dict[str, Any]:
        """Build Trello's query authentication without requiring a live client."""
        return {"key": key, "token": token, **extra}


class TrelloApi:
    """Typed Trello operations over an injectable JSON client."""

    def __init__(self, client: JsonClient, key: str, token: str) -> None:
        self._client = client
        self._key = key
        self._token = token

    def _auth(self, params: QueryParams | None = None) -> dict[str, Any]:
        return TrelloClient.auth_params_for(self._key, self._token, **dict(params or {}))

    def current_member(self) -> MemberWire:
        """Return the authenticated Trello account."""
        return parse_json(
            self._client.get(routes.CURRENT_MEMBER, self._auth({"fields": "id,fullName,username,email"})),
            MemberWire,
        )

    def boards(self) -> list[BoardWire]:
        """Return open boards visible to the account."""
        return parse_json(
            self._client.get(
                routes.BOARDS,
                self._auth({"fields": "id,name,desc,url,closed", "filter": "open"}),
            ),
            BoardsWire,
        ).root

    def lists(self, board_id: str) -> list[ListWire]:
        """Return open lists on a board."""
        return parse_json(
            self._client.get(
                routes.lists(board_id), self._auth({"fields": "id,name,pos", "filter": "open"})
            ),
            ListsWire,
        ).root

    def cards(self, board_id: str, *, fields: str) -> list[CardWire]:
        """Return cards on a board."""
        return parse_json(
            self._client.get(routes.cards(board_id), self._auth(_card_read_params(fields))), CardsWire
        ).root

    def card(self, card_id: str, *, fields: str) -> CardWire:
        """Return one card."""
        return parse_json(
            self._client.get(routes.card(card_id), self._auth(_card_read_params(fields))), CardWire
        )

    def create_card(self, params: QueryParams) -> CardWire:
        """Create and validate one card."""
        return parse_json(self._client.post(routes.CARDS, params=self._auth(params)), CardWire)

    def update_card(self, card_id: str, params: QueryParams) -> None:
        """Update one card."""
        self._client.put(routes.card(card_id), params=self._auth(params))

    def members(self, board_id: str) -> list[MemberWire]:
        """Return members assignable on a board."""
        return parse_json(
            self._client.get(
                routes.members(board_id), self._auth({"fields": "id,fullName,username"})
            ),
            MembersWire,
        ).root

    def labels(self, board_id: str) -> list[LabelWire]:
        """Return labels available on a board."""
        return parse_json(
            self._client.get(
                routes.labels(board_id), self._auth({"fields": "id,name,color", "limit": 1000})
            ),
            LabelsWire,
        ).root

    def comments(self, card_id: str) -> Iterator[CommentActionWire]:
        """Yield commentCard actions using Trello's stable before cursor."""

        before = ""
        for _ in range(1000):
            extra: dict[str, Any] = {
                "filter": "commentCard",
                "limit": 1000,
                "memberCreator": "true",
                "memberCreator_fields": "id,fullName,username",
            }
            if before:
                extra["before"] = before
            actions = parse_json(
                self._client.get(
                    routes.actions(card_id),
                    self._auth(extra),
                ),
                ActionsWire,
            ).root
            yield from actions
            if len(actions) < 1000:
                return
            next_before = actions[-1].id
            if not next_before or next_before == before:
                raise PaginationError("Trello repeated or omitted a comment action cursor")
            before = next_before
        raise PaginationError("Trello comment pagination exceeded 1000 pages")

    def create_comment(self, card_id: str, text: str) -> CommentActionWire:
        """Append one card comment without automatic replay."""

        return parse_json(
            self._client.post(
                routes.comments(card_id),
                params=self._auth({"text": text}),
            ),
            CommentActionWire,
        )


__all__ = ["TrelloApi", "TrelloClient"]
