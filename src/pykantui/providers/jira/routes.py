"""Jira Cloud REST route construction."""

CURRENT_USER = "/rest/api/3/myself"
PROJECTS = "/rest/api/3/project/search"
BOARDS = "/rest/agile/1.0/board"
SEARCH = "/rest/api/3/search/jql"
CREATE_ISSUE = "/rest/api/2/issue"
ASSIGNABLE_USERS = "/rest/api/3/user/assignable/search"


def board_configuration(board_id: str) -> str:
    """Return one agile-board configuration route."""
    return f"/rest/agile/1.0/board/{board_id}/configuration"


def project_statuses(project_key: str) -> str:
    """Return the statuses available in one project."""
    return f"/rest/api/3/project/{project_key}/statuses"


def issue(key: str) -> str:
    """Return the v3 route for one issue."""
    return f"/rest/api/3/issue/{key}"


def issue_update(key: str) -> str:
    """Return the v2 update route used for string descriptions."""
    return f"/rest/api/2/issue/{key}"


def comments(key: str) -> str:
    """Return the v3 comment collection for one issue."""

    return f"/rest/api/3/issue/{key}/comment"


def transitions(key: str) -> str:
    """Return the transition collection for one issue."""
    return f"/rest/api/3/issue/{key}/transitions"


def issue_types(project_id: str) -> str:
    """Return create metadata issue types for one project."""
    return f"/rest/api/3/issue/createmeta/{project_id}/issuetypes"


def project_components(project_id_or_key: str) -> str:
    """Return the paginated components route for one project."""
    return f"/rest/api/3/project/{project_id_or_key}/component"
