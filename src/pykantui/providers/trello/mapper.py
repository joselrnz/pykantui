"""Pure conversion from Trello wire models to neutral tracker models."""

from __future__ import annotations

from collections.abc import Mapping

from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import float_or_none, parse_date, parse_datetime

from .schemas import BoardWire, CardWire, CommentActionWire, MemberWire


def member_to_remote(member: MemberWire) -> RemoteUser:
    """Map the authenticated Trello member."""
    return RemoteUser(
        account_id=member.id,
        display_name=member.fullName or member.username,
        username=member.username,
        email=member.email or "",
    )


def board_to_remote(board: BoardWire) -> RemoteProject:
    """Map one Trello board."""
    return RemoteProject(
        project_id=board.id,
        key=board.name,
        name=board.name,
        description=board.desc,
        url=board.url,
    )


def card_to_remote(
    card: CardWire,
    names: Mapping[str, str],
    members: Mapping[str, str] | None = None,
) -> RemoteIssue:
    """Map a card using board-wide list and member directories."""
    due = parse_datetime(card.due)
    directory = members or {}
    assignees = [directory.get(member_id, "") for member_id in card.idMembers]
    creation = next(
        (
            action
            for action in card.actions
            if action is not None
            and action.idMemberCreator
            and action.type in ("", "createCard", "copyCard")
        ),
        None,
    )
    creator = creation.memberCreator if creation is not None else None
    reporter_id = creation.idMemberCreator if creation is not None else ""
    reporter = (
        (creator.fullName or creator.username)
        if creator is not None
        else directory.get(reporter_id, "")
    )
    return RemoteIssue(
        issue_id=card.id,
        key=f"CARD-{card.idShort}" if card.idShort is not None else card.id,
        title=card.name,
        column_id=card.idList,
        body=card.desc,
        status=names.get(card.idList, ""),
        assignee=", ".join(name for name in assignees if name),
        reporter=reporter,
        assignee_ids=tuple(card.idMembers),
        reporter_id=reporter_id,
        labels=tuple(label.name or label.color for label in card.labels if label.name or label.color),
        updated_at=parse_datetime(card.dateLastActivity),
        finished_at=due if card.dueComplete else None,
        due_date=parse_date(card.due),
        position=float_or_none(card.pos),
        url=card.url,
        extra={"short_id": card.idShort},
    )


def action_to_remote(
    action: CommentActionWire,
    issue_id: str,
    members: Mapping[str, str] | None = None,
) -> RemoteComment:
    """Map exactly one Trello commentCard action."""

    creator = action.memberCreator
    author_id = action.idMemberCreator
    author = (
        (creator.fullName or creator.username)
        if creator is not None
        else (members or {}).get(author_id, "")
    )
    return RemoteComment(
        comment_id=action.id,
        issue_id=issue_id,
        body=action.data.text,
        author=author,
        author_id=author_id,
        created_at=parse_datetime(action.date),
    )


__all__ = ["action_to_remote", "board_to_remote", "card_to_remote", "member_to_remote"]
