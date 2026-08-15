"""GitHub provider package."""

from .provider import GitHubProvider, is_pull_request

__all__ = ["GitHubProvider", "is_pull_request"]
