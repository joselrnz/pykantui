"""Forgejo REST route construction."""

from __future__ import annotations

from urllib.parse import quote

CURRENT_USER = "/user"
REPOSITORIES = "/user/repos"


def labels(repository: str) -> str:
    return f"{_repository(repository)}/labels"


def issues(repository: str) -> str:
    return f"{_repository(repository)}/issues"


def issue(repository: str, number: object) -> str:
    return f"{issues(repository)}/{quote(str(number), safe='')}"


def issue_comments(repository: str, number: object) -> str:
    return f"{issue(repository, number)}/comments"


def issue_labels(repository: str, number: object) -> str:
    return f"{issue(repository, number)}/labels"


def _repository(repository: str) -> str:
    parts = repository.strip().split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("Forgejo repository must be owner/name")
    owner, name = (quote(part, safe="") for part in parts)
    return f"/repos/{owner}/{name}"


__all__ = ["CURRENT_USER", "REPOSITORIES", "issue", "issue_comments", "issue_labels", "issues", "labels"]
