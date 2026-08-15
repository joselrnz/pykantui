"""A small JSON-over-HTTP client, on httpx.

One :class:`httpx.Client` per provider, held open for the life of the sync.
That is the point of using it: an export makes one request per page plus a
handful of lookups, and a pooled connection avoids paying for a TLS handshake
on every one of them. It also gives us sane timeouts, explicit redirect refusal, and
HTTP/2-ready transports without hand-rolling any of it.

Retries cover safe reads that fail transiently -- 429, 5xx and dropped
connections -- and nothing else. Provider writes are never replayed: a timed
out create may already have succeeded remotely, and retrying it can duplicate
the card. A 401 will not succeed on the second attempt either.
``httpx``'s own ``transport retries`` only covers connection errors, so the
status-code half of that is the loop in :meth:`JsonHttp.request`.

Errors are translated at this boundary into
:mod:`pykantui.tracker.errors`, so nothing above here ever catches an
``httpx`` exception or has to know which library did the talking.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Self

import httpx

from pykantui.api.cache import CacheEntry, ResponseCache
from pykantui.api.errors import (
    AuthError,
    NotFoundError,
    PayloadError,
    ProviderError,
    RateLimitError,
    TransportError,
)

from .redaction import redact
from .retry import RetryPolicy
from .types import HttpMethod, JsonValue, QueryParams, QueryScalar, ensure_json

#: Sent on every request. A provider that starts misbehaving should be
#: attributable in someone's access log.
USER_AGENT = "pykantui"

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3

#: Connection-level retries, handled inside the transport. Distinct from the
#: status-code retries in ``request``: this one covers a connection dropped
#: before any response arrived, where nothing was delivered and a replay is
#: unambiguously safe.
_TRANSPORT_RETRIES = 2

# HTTP-level replay is deliberately narrower than theoretical idempotency.
# Provider implementations do not all honour PUT/DELETE semantics perfectly,
# while these three methods cannot mutate a conforming endpoint.
_SAFE_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class JsonHttp:
    """A JSON client bound to one base URL and one set of headers."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        user_agent: str = USER_AGENT,
        client: httpx.Client | None = None,
        cache: ResponseCache | None = None,
        sensitive_values: tuple[str, ...] = (),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.retry_policy = retry_policy or RetryPolicy(retries=max(0, retries))
        self.retries = self.retry_policy.retries
        self._sleeper = sleeper
        header_values = [
            value
            for name, value in (headers or {}).items()
            if name.casefold() in {"authorization", "x-api-key", "shortcut-token"}
        ]
        self._sensitive_values = frozenset(
            [*header_values, *(auth[1:] if auth else ()), *sensitive_values]
        )

        #: Consulted only for GETs whose caller passed a ttl. None disables
        #: caching entirely, which is what tests and one-shot commands want.
        self.cache = cache
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={"Accept": "application/json", "User-Agent": user_agent, **dict(headers or {})},
            auth=auth,
            timeout=timeout,
            follow_redirects=False,
            transport=httpx.HTTPTransport(retries=_TRANSPORT_RETRIES),
        )

    # ---- construction helpers -------------------------------------------

    @classmethod
    def with_basic_auth(
        cls,
        base_url: str,
        username: str,
        password: str,
        *,
        headers: Mapping[str, str] | None = None,
        cache: ResponseCache | None = None,
        client: httpx.Client | None = None,
    ) -> Self:
        """Basic auth, sent up front on every request.

        httpx's ``auth=(user, pass)`` is preemptive, which is what we want:
        Jira Cloud answers some unauthenticated requests with a 200 and an
        anonymous body rather than a 401 challenge, so waiting to be challenged
        means silently reading someone else's empty view of the world.
        """
        return cls(
            base_url,
            auth=(username, password),
            headers=headers,
            cache=cache,
            client=client,
            sensitive_values=(password,),
        )

    @classmethod
    def with_header_key(
        cls,
        base_url: str,
        header: str,
        value: str,
        *,
        headers: Mapping[str, str] | None = None,
        cache: ResponseCache | None = None,
        client: httpx.Client | None = None,
    ) -> Self:
        """A bare API-key header, the way Plane's ``X-API-Key`` works.

        Also covers the trackers that put a naked token in ``Authorization``
        with no scheme word -- Linear, ClickUp and Monday all do this, and it
        is the single most common thing to get wrong about them.
        """
        merged_headers = {header: value, **dict(headers or {})}
        return cls(
            base_url,
            headers=merged_headers,
            cache=cache,
            client=client,
            sensitive_values=(value,),
        )

    @classmethod
    def with_bearer(
        cls,
        base_url: str,
        token: str,
        *,
        headers: Mapping[str, str] | None = None,
        cache: ResponseCache | None = None,
        client: httpx.Client | None = None,
    ) -> Self:
        """``Authorization: Bearer <token>`` -- GitHub, Asana, Notion."""
        merged_headers = {"Authorization": f"Bearer {token}", **dict(headers or {})}
        return cls(
            base_url,
            headers=merged_headers,
            cache=cache,
            client=client,
            sensitive_values=(token,),
        )

    # ---- requests --------------------------------------------------------

    def get(
        self,
        path: str,
        params: QueryParams | None = None,
        *,
        ttl: float = 0.0,
        label: str = "",
    ) -> JsonValue:
        """A GET, optionally served from or revalidated against the cache.

        ``ttl`` is opt-in per call rather than a client-wide default, because
        only the caller knows how stale an answer may be: a column layout is
        good for hours, an issue list for a minute, a transition lookup for no
        time at all.
        """
        return self.request("GET", path, params=params, ttl=ttl, label=label)

    def post(self, path: str, body: JsonValue = None, params: QueryParams | None = None) -> JsonValue:
        return self.request("POST", path, body=body, params=params)

    def put(self, path: str, body: JsonValue = None, params: QueryParams | None = None) -> JsonValue:
        return self.request("PUT", path, body=body, params=params)

    def patch(self, path: str, body: JsonValue = None, params: QueryParams | None = None) -> JsonValue:
        return self.request("PATCH", path, body=body, params=params)

    def delete(self, path: str, params: QueryParams | None = None) -> JsonValue:
        """Delete one resource without replaying a potentially accepted call."""
        return self.request("DELETE", path, params=params)

    def graphql(self, query: str, variables: Mapping[str, JsonValue] | None = None, *, path: str = "") -> JsonValue:
        """Run a GraphQL query and return its ``data``.

        GraphQL's failure mode is the reason this is not just a ``post``: a
        query that fails comes back **HTTP 200** with an ``errors`` array and a
        null ``data``. Status-code handling alone sees success and hands the
        caller an empty result, so the payload has to be inspected too.
        """
        document = self.request("POST", path or "", body={"query": query, "variables": dict(variables or {})})
        if not isinstance(document, dict):
            raise ProviderError("GraphQL endpoint returned an unexpected payload")

        errors = document.get("errors")
        if errors:
            message = redact(_graphql_message(errors), self._sensitive_values)
            if document.get("data") is not None:
                raise PayloadError(
                    f"GraphQL returned partial data with errors; write outcome may be unknown: {message}"
                )
            # A GraphQL API reports "not authorised" in the body, at 200, so
            # the auth case has to be recovered from the text to give the
            # caller the error type it would have got from a REST endpoint.
            if any(word in message.lower() for word in ("unauthor", "authentic", "forbidden", "token")):
                raise AuthError(f"GraphQL request was rejected: {message}")
            raise ProviderError(f"GraphQL request failed: {message}")
        return document.get("data")

    def request(
        self,
        method: HttpMethod | str,
        path: str,
        *,
        params: QueryParams | None = None,
        body: JsonValue = None,
        ttl: float = 0.0,
        label: str = "",
    ) -> JsonValue:
        query = _clean_params(params)
        retryable = method.upper() in _SAFE_RETRY_METHODS

        # Only GETs are cacheable, and only when the caller said how long for.
        cache_key = ""
        entry = None
        if self.cache is not None and method.upper() == "GET" and ttl > 0:
            cache_key = self.cache.key_for(method, path, query, label)
            entry = self.cache.get(cache_key)
            if entry is not None and entry.is_fresh(ttl):
                self.cache.hits += 1
                return entry.body

        last_error: ProviderError | None = None

        for attempt in range(self.retries + 1):
            try:
                return self._once(method, path, query, body, cache_key=cache_key, entry=entry)
            except RateLimitError as error:
                if not retryable:
                    raise
                last_error = error
                delay = self.retry_policy.delay(attempt, retry_after=error.retry_after)
            except TransportError as error:
                if not retryable:
                    raise
                last_error = error
                delay = self.retry_policy.delay(attempt)
            except ProviderError:
                # Auth failures and 4xx generally: retrying changes nothing, so
                # fail now rather than after three more round trips.
                raise
            if attempt < self.retries:
                self._sleeper(delay)

        assert last_error is not None  # noqa: S101 - the loop always sets it before falling through
        raise last_error

    def _once(
        self,
        method: str,
        path: str,
        params: dict[str, QueryScalar | str],
        body: JsonValue,
        *,
        cache_key: str = "",
        entry: CacheEntry | None = None,
    ) -> JsonValue:
        # A stale entry with a validator turns a full fetch into a 304.
        headers = entry.validators() if entry is not None else {}

        try:
            response = self._client.request(
                method,
                path,
                params=params or None,
                json=body,
                headers=headers or None,
                follow_redirects=False,
            )
        except httpx.TimeoutException as error:
            raise TransportError(f"{_host(error)} timed out") from error
        except httpx.TransportError as error:
            raise TransportError(
                f"could not reach {_host(error)}: {redact(error, self._sensitive_values)}"
            ) from error

        if response.status_code == 304 and entry is not None:
            # Unchanged. Cost: one round trip and no payload.
            if self.cache is not None:
                self.cache.revalidations += 1
                if cache_key:
                    self.cache.touch(cache_key, entry)
            return entry.body

        if response.is_redirect:
            target = response.headers.get("Location", "another URL")
            raise ProviderError(
                f"{response.request.url.host} returned an HTTP redirect to {_redirect_host(target)}; refused",
                hint="Update the provider base URL explicitly instead of forwarding credentials through redirects.",
            )

        if response.status_code >= 400:
            raise _from_response(response, self._sensitive_values)

        parsed: JsonValue = None
        if response.content:
            try:
                parsed = ensure_json(response.json())
            except ValueError as error:
                raise ProviderError(f"{response.request.url.host} returned something that is not JSON") from error

        if self.cache is not None and cache_key:
            self.cache.misses += 1
            self.cache.put(
                cache_key,
                parsed,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
            )
        return parsed

    # ---- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JsonHttp:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _from_response(response: httpx.Response, sensitive_values: frozenset[str] = frozenset()) -> ProviderError:
    """Turn a failed response into the right error, with a usable hint."""
    status = response.status_code
    where = response.request.url.host
    detail = _detail(response, sensitive_values)

    if status in (401, 403):
        return AuthError(
            f"{where} rejected the credentials ({status}){detail}",
            hint=(
                "Check the token and, for Jira Cloud, that it is paired with the account "
                "email — Cloud uses basic auth (email + API token), not a bearer token."
            ),
        )
    if status == 404:
        return NotFoundError(f"{where} has no such resource (404){detail}")
    if status == 429:
        return RateLimitError(
            f"{where} is rate limiting us (429){detail}",
            retry_after=_retry_after(response),
            hint="Slow the sync down, or wait and re-run it.",
        )
    if status >= 500:
        return TransportError(f"{where} returned a server error ({status}){detail}")
    return ProviderError(f"{where} refused the request ({status}){detail}")


def _clean_params(params: QueryParams | None) -> dict[str, QueryScalar | str]:
    """Drop unset values and flatten sequences.

    ``None`` means "not supplied" throughout the providers -- the first page of
    a cursor loop passes ``cursor=None`` -- and httpx would otherwise serialise
    it as the literal string "None".
    """
    if not params:
        return {}
    cleaned: dict[str, QueryScalar | str] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = ",".join(str(item) for item in value) if isinstance(value, (list, tuple)) else value
    return cleaned


def _graphql_message(errors: JsonValue) -> str:
    """The readable part of a GraphQL ``errors`` array."""
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)
        return str(first)
    return str(errors)


def _host(error: Exception) -> str:
    request = getattr(error, "request", None)
    return str(request.url.host) if request is not None else "the server"


def _retry_after(response: httpx.Response) -> float | None:
    try:
        raw = response.headers.get("Retry-After")
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _detail(response: httpx.Response, sensitive_values: frozenset[str] = frozenset()) -> str:
    """The provider's own explanation, where it gave one worth repeating."""
    try:
        body = response.text
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return ""
    if not body:
        return ""
    try:
        document = response.json()
    except ValueError:
        return f": {redact(body[:200], sensitive_values)}"

    if isinstance(document, dict):
        for key in ("errorMessages", "message", "detail", "error", "errors"):
            value = document.get(key)
            if isinstance(value, list) and value:
                return f": {redact(value[0], sensitive_values)[:200]}"
            if isinstance(value, str) and value:
                return f": {redact(value, sensitive_values)[:200]}"
    return f": {redact(body[:200], sensitive_values)}"


def _redirect_host(location: str) -> str:
    try:
        return str(httpx.URL(location).host or "another URL")
    except (TypeError, ValueError):
        return "another URL"
