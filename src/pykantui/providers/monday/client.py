"""Authenticated transport and typed Monday.com GraphQL operations."""

from collections.abc import Iterator
from typing import Self

from pykantui.api import (
    JsonClient,
    JsonHttp,
    JsonObject,
    PaginationError,
    PayloadError,
    ResponseCache,
    parse_json,
)

from . import operations
from .schemas import (
    BoardsDataWire,
    BoardShapesDataWire,
    BoardShapeWire,
    BoardSummaryWire,
    CreateItemDataWire,
    CreateUpdateDataWire,
    ItemsDataWire,
    ItemUpdatesDataWire,
    ItemWire,
    MeDataWire,
    UpdateWire,
    UsersDataWire,
    UserWire,
)


class MondayClient(JsonHttp):
    """Monday.com requires a bare token and an explicit API version."""

    @classmethod
    def connect(
        cls,
        base_url: str,
        token: str,
        *,
        api_version: str,
        cache: ResponseCache | None = None,
    ) -> Self:
        return cls(
            base_url,
            headers={"Authorization": token, "API-Version": api_version},
            cache=cache,
        )


class MondayApi:
    """Typed Monday operations over an injectable GraphQL transport."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    def viewer(self) -> UserWire:
        """Return the authenticated Monday user."""
        return parse_json(self._client.graphql(operations.ME_QUERY), MeDataWire).me

    def boards(self) -> Iterator[BoardSummaryWire]:
        """Yield every active board across page-number pagination."""
        for page_number in range(1, 100):
            response = self._client.graphql(
                operations.BOARDS_QUERY,
                {"page": page_number},
            )
            boards = parse_json(response, BoardsDataWire).boards
            yield from boards
            if len(boards) < 100:
                return

    def board_shape(self, board_id: str) -> BoardShapeWire | None:
        """Return one board's groups and columns."""
        response = self._client.graphql(
            operations.BOARD_SHAPE_QUERY,
            {"ids": [board_id]},
        )
        boards = parse_json(response, BoardShapesDataWire).boards
        return boards[0] if boards else None

    def items(self, board_id: str) -> Iterator[ItemWire]:
        """Yield every item on one board across cursor pages."""
        cursor: str | None = None
        while True:
            response = self._client.graphql(
                operations.ITEMS_QUERY,
                {"ids": [board_id], "cursor": cursor},
            )
            boards = parse_json(response, BoardShapesDataWire).boards
            if not boards:
                return
            page = boards[0].items_page
            yield from page.items
            if not page.cursor:
                return
            cursor = page.cursor

    def item(self, item_id: str) -> ItemWire | None:
        """Return one item by id."""
        response = self._client.graphql(
            operations.ONE_ITEM_QUERY,
            {"ids": [item_id]},
        )
        items = parse_json(response, ItemsDataWire).items
        return items[0] if items else None

    def create_item(self, variables: JsonObject) -> str:
        """Create an item and return its id."""
        response = self._client.graphql(operations.CREATE_MUTATION, variables)
        result = parse_json(response, CreateItemDataWire)
        return str(result.create_item.id)

    def change_columns(self, item_id: str, board_id: str, values: str) -> None:
        """Update several item columns in one mutation."""
        self._client.graphql(
            operations.CHANGE_MULTIPLE_MUTATION,
            {"item": item_id, "board": board_id, "values": values},
        )

    def rename_item(self, item_id: str, board_id: str, title: str) -> None:
        """Rename one item."""
        self._client.graphql(
            operations.RENAME_MUTATION,
            {"item": item_id, "board": board_id, "value": title},
        )

    def users(self) -> list[UserWire]:
        """Return users visible to the token."""
        response = self._client.graphql(operations.USERS_QUERY)
        return parse_json(response, UsersDataWire).users

    def move_to_group(self, item_id: str, group_id: str) -> None:
        """Move an item between board groups."""
        self._client.graphql(
            operations.MOVE_GROUP_MUTATION,
            {"item": item_id, "group": group_id},
        )

    def move_to_status(
        self,
        item_id: str,
        board_id: str,
        column_id: str,
        value: str,
    ) -> None:
        """Change an item's status-column value."""
        self._client.graphql(
            operations.MOVE_MUTATION,
            {
                "item": item_id,
                "board": board_id,
                "column": column_id,
                "value": value,
            },
        )

    def updates(self, item_id: str) -> Iterator[UpdateWire]:
        """Yield all item updates across Monday page-number pagination."""

        for page in range(1, 1001):
            response = self._client.graphql(
                operations.UPDATES_QUERY,
                {"ids": [item_id], "page": page},
            )
            items = parse_json(response, ItemUpdatesDataWire).items
            if len(items) != 1 or str(items[0].id) != item_id:
                raise PayloadError("Monday omitted or replaced the requested item in its updates response")
            updates = items[0].updates
            yield from updates
            if len(updates) < 100:
                return
        raise PaginationError("Monday comment pagination exceeded 1000 pages")

    def create_update(self, item_id: str, body: str) -> UpdateWire:
        """Append one item update without automatic replay."""

        response = self._client.graphql(
            operations.CREATE_UPDATE_MUTATION,
            {"item": item_id, "body": body},
        )
        return parse_json(response, CreateUpdateDataWire).create_update


__all__ = ["MondayApi", "MondayClient"]
