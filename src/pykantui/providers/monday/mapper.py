"""Pure conversion from Monday wire models to neutral tracker models."""

from __future__ import annotations

import json
from collections.abc import Mapping

from pykantui.tracker.markup import to_markdown
from pykantui.tracker.models import RemoteComment, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.util import parse_date, parse_datetime

from .schemas import BoardSummaryWire, ColumnValueWire, ItemWire, UpdateWire, UserWire


def user_to_remote(user: UserWire) -> RemoteUser:
    """Map the authenticated Monday user."""
    return RemoteUser(
        account_id=str(user.id),
        display_name=user.name,
        email=user.email,
    )


def board_to_remote(board: BoardSummaryWire) -> RemoteProject:
    """Map a Monday board to the neutral project container."""
    board_id = str(board.id)
    return RemoteProject(
        project_id=board_id,
        key=board.name,
        name=board.name,
        description=to_markdown(board.description),
        url=board.url or f"https://view.monday.com/boards/{board_id}",
    )


def item_to_remote(
    item: ItemWire,
    project_id: str,
    axis_id: str,
    labels: Mapping[str, str],
    configured_columns: Mapping[str, str],
) -> RemoteIssue:
    """Map one item using the board's configured semantic columns."""
    values = {value.id: value for value in item.column_values}
    if axis_id and axis_id in values:
        index = status_index(values[axis_id])
        column_id = index
        status = labels.get(index, values[axis_id].text)
    else:
        column_id = item.group.id
        status = item.group.title

    configured = {
        name: values.get(column_id, ColumnValueWire())
        for name, column_id in configured_columns.items()
        if column_id
    }

    def text_value(name: str) -> str:
        return configured.get(name, ColumnValueWire()).text.strip()

    item_id = str(item.id)
    creator = item.creator or UserWire()
    return RemoteIssue(
        issue_id=item_id,
        key=item_id,
        title=item.name,
        column_id=column_id,
        body=text_value("body"),
        issue_type=text_value("issue_type"),
        status=status,
        priority=text_value("priority"),
        assignee=text_value("assignee"),
        reporter=creator.name,
        assignee_ids=people_ids(configured.get("assignee")),
        reporter_id=str(creator.id) if str(creator.id) else "",
        labels=tuple(
            label.strip()
            for label in text_value("labels").split(",")
            if label.strip()
        ),
        created_at=parse_datetime(item.created_at),
        updated_at=parse_datetime(item.updated_at),
        due_date=parse_date(text_value("due_date")),
        url=item.url or f"https://view.monday.com/boards/{project_id}/pulses/{item_id}",
        extra={"group": item.group.title},
    )


def labels_from(settings: object) -> dict[str, str]:
    """Decode status-index labels from a nested settings JSON string."""
    if not settings:
        return {}
    try:
        document = json.loads(settings) if isinstance(settings, str) else settings
    except (TypeError, ValueError):
        return {}
    labels = document.get("labels") if isinstance(document, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {
        str(index): str(label)
        for index, label in labels.items()
        if str(label).strip()
    }


def status_index(value: ColumnValueWire) -> str:
    """Return the stable numeric index behind a status value."""
    if value.value:
        try:
            document = json.loads(value.value)
            if isinstance(document, dict) and document.get("index") is not None:
                return str(document["index"])
        except (TypeError, ValueError):
            pass
    return value.text


def update_to_remote(
    update: UpdateWire,
    issue_id: str,
    *,
    parent_id: str = "",
) -> RemoteComment:
    """Map a Monday update while preferring its provider-produced plain text."""

    creator = update.creator or UserWire()
    return RemoteComment(
        comment_id=str(update.id),
        issue_id=issue_id,
        body=update.text_body or to_markdown(update.body),
        author=creator.name,
        author_id=str(creator.id or update.creator_id),
        created_at=parse_datetime(update.created_at),
        updated_at=parse_datetime(update.updated_at or update.edited_at),
        parent_id=parent_id,
    )


def people_ids(value: ColumnValueWire | None) -> tuple[str, ...]:
    """Extract person ids from a Monday people-column JSON string."""
    if value is None or value.type not in ("people", "person") or not value.value:
        return ()
    try:
        parsed = json.loads(value.value)
    except (TypeError, ValueError):
        return ()
    people = parsed.get("personsAndTeams", []) if isinstance(parsed, dict) else []
    return tuple(
        str(entry.get("id", ""))
        for entry in people
        if isinstance(entry, dict)
        and entry.get("id")
        and entry.get("kind", "person") == "person"
    )


__all__ = [
    "board_to_remote",
    "item_to_remote",
    "labels_from",
    "people_ids",
    "status_index",
    "update_to_remote",
    "user_to_remote",
]
