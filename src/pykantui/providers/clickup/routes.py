"""ClickUp REST route construction."""

CURRENT_USER = "/user"
TEAMS = "/team"


def spaces(team_id: str) -> str:
    """Return spaces inside a workspace/team."""
    return f"/team/{team_id}/space"


def space_lists(space_id: str) -> str:
    """Return folderless lists inside a space."""
    return f"/space/{space_id}/list"


def folders(space_id: str) -> str:
    """Return folders and their embedded lists inside a space."""
    return f"/space/{space_id}/folder"


def list_(list_id: str) -> str:
    """Return one ClickUp list."""
    return f"/list/{list_id}"


def tasks(list_id: str) -> str:
    """Return or create tasks in a ClickUp list."""
    return f"/list/{list_id}/task"


def task(task_id: str) -> str:
    """Return one ClickUp task."""
    return f"/task/{task_id}"


def task_comments(task_id: str) -> str:
    """Return the comment collection for one task."""
    return f"{task(task_id)}/comment"


def comment_replies(comment_id: str) -> str:
    """Return threaded replies beneath one task comment."""
    return f"/comment/{comment_id}/reply"


def custom_item_types(team_id: str) -> str:
    """Return custom task types configured in a workspace/team."""
    return f"/team/{team_id}/custom_item"


def members(list_id: str) -> str:
    """Return assignable members for a list."""
    return f"/list/{list_id}/member"


def task_tag(task_id: str, label: str) -> str:
    """Return the task-tag mutation route."""
    return f"/task/{task_id}/tag/{label}"
