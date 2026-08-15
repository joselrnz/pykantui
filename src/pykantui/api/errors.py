"""One error vocabulary for every outbound provider API.

The wizard and the CLI need to tell "your token is wrong" apart from "the
network is down" apart from "that project does not exist", and they must do it
without knowing whether the provider underneath speaks Jira or Trello. So each
provider translates its own failures into these, once, at its edge.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base for anything a provider raises on purpose."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        #: What to do about it, in one line. Printed under the error, so a
        #: failure ends with a next step rather than just a diagnosis.
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n{self.hint}" if self.hint else base


class AuthError(ProviderError):
    """Credentials missing, wrong, or of the wrong kind.

    The wrong-kind case is worth its own hint: a Jira *Server* personal access
    token and a Jira *Cloud* API token look identical and fail identically.
    """


class NotFoundError(ProviderError):
    """The project, board or issue is not there, or not visible to this account."""


class RateLimitError(ProviderError):
    """The provider is throttling us."""

    def __init__(self, message: str, *, retry_after: float | None = None, hint: str = "") -> None:
        super().__init__(message, hint=hint)
        self.retry_after = retry_after


class TransportError(ProviderError):
    """The request never got a usable answer: DNS, TLS, timeout, connection reset."""


class PayloadError(ProviderError):
    """The provider returned JSON that does not match its documented shape."""


class PaginationError(ProviderError):
    """A provider's paging contract would return an incomplete collection."""


class UnsupportedError(ProviderError):
    """A capability this provider does not have was asked for anyway.

    Raised rather than returned because the UI is supposed to consult
    :class:`~pykantui.tracker.spec.Capabilities` and not offer the action at
    all -- reaching here is a bug in the caller, not a user mistake.
    """
