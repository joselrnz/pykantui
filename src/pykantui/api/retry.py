"""Bounded retry policy for transient provider failures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential retry configuration shared by all HTTP clients."""

    retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValueError("retries cannot be negative")
        if self.base_delay < 0:
            raise ValueError("base_delay cannot be negative")
        if self.max_delay < 0:
            raise ValueError("max_delay cannot be negative")

    def delay(self, attempt: int, *, retry_after: float | None = None) -> float:
        """Return the bounded delay for a zero-based retry attempt."""
        suggested = retry_after if retry_after is not None and retry_after >= 0 else self.base_delay * (2**attempt)
        return min(float(suggested), self.max_delay)


__all__ = ["RetryPolicy"]
