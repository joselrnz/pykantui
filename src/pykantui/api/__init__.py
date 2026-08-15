"""Shared outbound API infrastructure for every provider.

Provider packages own authentication, routes, response mapping and payloads.
This package owns only transport concerns that are identical across services:
JSON-over-HTTP, caching, pagination and normalized operational errors.
"""

from .cache import TTL_ISSUES, TTL_NONE, TTL_STRUCTURE, CacheEntry, ResponseCache
from .client import JsonHttp
from .errors import (
    AuthError,
    NotFoundError,
    PaginationError,
    PayloadError,
    ProviderError,
    RateLimitError,
    TransportError,
)
from .pagination import (
    page_by_cursor,
    page_by_next_cursor,
    page_by_number,
    page_by_offset,
    page_by_token,
    page_objects_by_cursor,
    page_objects_by_offset,
    page_objects_by_token,
)
from .retry import RetryPolicy
from .types import (
    HttpMethod,
    JsonArray,
    JsonClient,
    JsonObject,
    JsonValue,
    QueryParams,
    QueryValue,
    ensure_json,
    expect_array,
    expect_object,
    expect_object_array,
    parse_json,
)

__all__ = [
    "AuthError",
    "CacheEntry",
    "HttpMethod",
    "JsonClient",
    "JsonHttp",
    "JsonArray",
    "JsonObject",
    "JsonValue",
    "NotFoundError",
    "PaginationError",
    "PayloadError",
    "ProviderError",
    "RateLimitError",
    "RetryPolicy",
    "ResponseCache",
    "QueryParams",
    "QueryValue",
    "TTL_ISSUES",
    "TTL_NONE",
    "TTL_STRUCTURE",
    "TransportError",
    "ensure_json",
    "expect_array",
    "expect_object",
    "expect_object_array",
    "page_by_cursor",
    "page_by_next_cursor",
    "page_by_number",
    "page_by_offset",
    "page_by_token",
    "page_objects_by_cursor",
    "page_objects_by_offset",
    "page_objects_by_token",
    "parse_json",
]
