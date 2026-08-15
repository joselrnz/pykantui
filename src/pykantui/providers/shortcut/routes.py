"""Shortcut REST route construction."""

from pykantui.api.errors import PayloadError

CURRENT_MEMBER = "/member"
MEMBERS = "/members"
WORKFLOWS = "/workflows"
STORIES = "/stories"
SEARCH_STORIES = "/search/stories"
_API_PREFIX = "/api/v3"


def search_continuation(value: str) -> str:
    """Return a same-endpoint continuation relative to our v3 base URL.

    Shortcut returns ``next`` as ``/api/v3/search/stories?...``.  HTTPX joins
    paths to a base URL that already ends in ``/api/v3``, so passing that value
    through unchanged requests ``/api/v3/api/v3/...``.  Validate the endpoint
    before trimming the duplicate prefix; this also prevents a provider
    payload from forwarding the API token to an arbitrary absolute URL.
    """

    path = value.removeprefix(_API_PREFIX)
    endpoint, separator, _query = path.partition("?")
    if endpoint != SEARCH_STORIES or not separator:
        raise PayloadError("Shortcut returned an invalid story-search continuation path")
    return path


def story(story_id: str) -> str:
    """Return the route for one story."""
    return f"/stories/{story_id}"


def comments(story_id: str) -> str:
    """Return the comment collection for one story."""

    return f"/stories/{story_id}/comments"
