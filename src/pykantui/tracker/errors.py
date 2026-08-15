"""Compatibility imports for provider errors now owned by ``pykantui.api``."""

from pykantui.api.errors import (
    AuthError,
    NotFoundError,
    PaginationError,
    ProviderError,
    RateLimitError,
    TransportError,
    UnsupportedError,
)

__all__ = [
    "AuthError",
    "NotFoundError",
    "PaginationError",
    "ProviderError",
    "RateLimitError",
    "TransportError",
    "UnsupportedError",
]
