"""Provider-neutral pagination generators."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import TypeVar

from .errors import PaginationError
from .types import JsonObject, JsonValue, expect_array, expect_object

DocumentPage = Mapping[str, JsonValue]
_T = TypeVar("_T")


def page_by_token(
    fetch: Callable[[str | None], DocumentPage | None],
    *,
    token_key: str = "nextPageToken",
    items_key: str = "issues",
    last_key: str = "isLast",
    max_pages: int = 1000,
) -> Iterator[JsonValue]:
    """Yield token-paged items, as used by Jira Cloud search."""
    _validate_limits(max_pages=max_pages)
    token: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        _remember_marker(token, seen, "token")
        page = fetch(token)
        if not page:
            return
        yield from _items(page, items_key)
        next_token = page.get(token_key)
        token = str(next_token) if next_token else None
        if page.get(last_key) or not token:
            return
    raise _exhausted(max_pages)


def page_by_cursor(
    fetch: Callable[[str | None], DocumentPage | None],
    *,
    cursor_key: str = "next_cursor",
    more_key: str = "next_page_results",
    items_key: str = "results",
    max_pages: int = 1000,
) -> Iterator[JsonValue]:
    """Yield cursor-paged items when a separate flag marks the last page."""
    _validate_limits(max_pages=max_pages)
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        _remember_marker(cursor, seen, "cursor")
        page = fetch(cursor)
        if not page:
            return
        yield from _items(page, items_key)
        if not page.get(more_key):
            return
        next_cursor = page.get(cursor_key)
        cursor = str(next_cursor) if next_cursor else None
        if not cursor:
            raise PaginationError(
                "provider reported another page but returned a missing next cursor; sync is incomplete",
                hint="Nothing was pruned. Retry after the provider pagination issue is resolved.",
            )
    raise _exhausted(max_pages)


def page_by_offset(
    fetch: Callable[[int, int], DocumentPage | None],
    *,
    page_size: int = 50,
    items_key: str = "values",
    max_pages: int = 1000,
) -> Iterator[JsonValue]:
    """Yield offset-paged items using provider metadata when available.

    Some APIs cap ``maxResults`` below the requested size. Treating that as a
    final short page silently truncates results, so ``isLast``, ``total`` and
    the response's effective page size take precedence over the request size.
    """
    _validate_limits(max_pages=max_pages, page_size=page_size)
    start = 0
    for _ in range(max_pages):
        page = fetch(start, page_size)
        if not page:
            return
        items = _items(page, items_key)
        yield from items
        if not items or page.get("isLast") is True:
            return
        next_start = start + len(items)
        total = _positive_int(page.get("total"))
        if total is not None and next_start >= total:
            return
        effective_size = _positive_int(page.get("maxResults")) or page_size
        has_continuation = bool(page.get("nextPage")) or total is not None
        if len(items) < effective_size and not has_continuation:
            return
        start = next_start
    raise _exhausted(max_pages)


def page_objects_by_token(
    fetch: Callable[[str | None], DocumentPage | None],
    *,
    token_key: str = "nextPageToken",
    items_key: str = "issues",
    last_key: str = "isLast",
    max_pages: int = 1000,
) -> Iterator[JsonObject]:
    """Yield object items from a token-paged document."""
    for item in page_by_token(
        fetch,
        token_key=token_key,
        items_key=items_key,
        last_key=last_key,
        max_pages=max_pages,
    ):
        yield expect_object(item, context="token-paged provider item")


def page_objects_by_cursor(
    fetch: Callable[[str | None], DocumentPage | None],
    *,
    cursor_key: str = "next_cursor",
    more_key: str = "next_page_results",
    items_key: str = "results",
    max_pages: int = 1000,
) -> Iterator[JsonObject]:
    """Yield object items from a cursor-paged document."""
    for item in page_by_cursor(
        fetch,
        cursor_key=cursor_key,
        more_key=more_key,
        items_key=items_key,
        max_pages=max_pages,
    ):
        yield expect_object(item, context="cursor-paged provider item")


def page_objects_by_offset(
    fetch: Callable[[int, int], DocumentPage | None],
    *,
    page_size: int = 50,
    items_key: str = "values",
    max_pages: int = 1000,
) -> Iterator[JsonObject]:
    """Yield object items from an offset-paged document."""
    for item in page_by_offset(fetch, page_size=page_size, items_key=items_key, max_pages=max_pages):
        yield expect_object(item, context="offset-paged provider item")


def page_by_next_cursor(
    fetch: Callable[[str | None], tuple[list[_T], str | None]],
    *,
    max_pages: int = 1000,
) -> Iterator[_T]:
    """Yield items when the fetcher returns ``(items, next_cursor)``."""
    _validate_limits(max_pages=max_pages)
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        _remember_marker(cursor, seen, "cursor")
        items, cursor = fetch(cursor)
        yield from items or []
        if not cursor:
            return
    raise _exhausted(max_pages)


def page_by_number(
    fetch: Callable[[int], list[_T]],
    *,
    page_size: int = 100,
    first_page: int = 1,
    max_pages: int = 1000,
) -> Iterator[_T]:
    """Yield numbered pages until the provider returns a short page."""
    _validate_limits(max_pages=max_pages, page_size=page_size)
    for offset in range(max_pages):
        items = fetch(first_page + offset)
        if not items:
            return
        yield from items
        if len(items) < page_size:
            return
    raise _exhausted(max_pages)


def _items(page: DocumentPage, key: str) -> list[JsonValue]:
    value = page.get(key)
    if value is None:
        return []
    return expect_array(value, context=f"provider pagination field {key!r}")


def _validate_limits(*, max_pages: int, page_size: int | None = None) -> None:
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    if page_size is not None and page_size <= 0:
        raise ValueError("page_size must be positive")


def _positive_int(value: JsonValue | None) -> int | None:
    """Return positive integer paging metadata without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _remember_marker(marker: str | None, seen: set[str], kind: str) -> None:
    if marker is None:
        return
    if marker in seen:
        raise PaginationError(
            f"provider returned a repeated {kind}; sync is incomplete",
            hint="Nothing was pruned. Retry after the provider pagination issue is resolved.",
        )
    seen.add(marker)


def _exhausted(max_pages: int) -> PaginationError:
    return PaginationError(
        f"provider pagination reached the {max_pages}-page safety limit; sync is incomplete",
        hint="Nothing was pruned. Narrow the project or raise the audited pagination limit.",
    )


__all__ = [
    "page_by_cursor",
    "page_by_next_cursor",
    "page_by_number",
    "page_by_offset",
    "page_by_token",
    "page_objects_by_cursor",
    "page_objects_by_offset",
    "page_objects_by_token",
]
