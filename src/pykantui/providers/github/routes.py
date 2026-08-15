"""GitHub REST route construction.

Keeping paths here makes endpoint ownership explicit and prevents string
assembly from leaking into the neutral provider orchestration layer.
"""

CURRENT_USER = "/user"
REPOSITORIES = "/user/repos"


def labels(repository: str) -> str:
    """Labels visible in one repository."""
    return f"/repos/{repository}/labels"


def issues(repository: str) -> str:
    """Issue collection for one repository."""
    return f"/repos/{repository}/issues"


def issue(repository: str, number: object) -> str:
    """One issue by its repository-local number."""
    return f"{issues(repository)}/{number}"


def issue_comments(repository: str, number: object) -> str:
    """The conversation comments attached to one issue."""
    return f"{issue(repository, number)}/comments"


def issue_labels(repository: str, number: object) -> str:
    """The complete label collection assigned to one issue."""
    return f"{issue(repository, number)}/labels"


def issue_types(repository: str) -> str:
    """Organization issue types enabled for one repository."""
    return f"/repos/{repository}/issue-types"


__all__ = [
    "CURRENT_USER",
    "REPOSITORIES",
    "issue",
    "issue_comments",
    "issue_labels",
    "issue_types",
    "issues",
    "labels",
]
