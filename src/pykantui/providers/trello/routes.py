"""Trello REST route construction."""

CURRENT_MEMBER = "/members/me"
BOARDS = "/members/me/boards"
CARDS = "/cards"


def lists(board_id: str) -> str:
    """Return the open-list collection for a board."""
    return f"/boards/{board_id}/lists"


def cards(board_id: str) -> str:
    """Return the card collection for a board."""
    return f"/boards/{board_id}/cards"


def card(card_id: str) -> str:
    """Return the route for one card."""
    return f"/cards/{card_id}"


def actions(card_id: str) -> str:
    """Return the action collection for one card."""

    return f"/cards/{card_id}/actions"


def comments(card_id: str) -> str:
    """Return the append-comment action endpoint for one card."""

    return f"/cards/{card_id}/actions/comments"


def members(board_id: str) -> str:
    """Return board members."""
    return f"/boards/{board_id}/members"


def labels(board_id: str) -> str:
    """Return board labels."""
    return f"/boards/{board_id}/labels"
