"""The provider contract.

A provider answers five questions -- who am I, what projects are there, what
columns does one have, what issues does it hold, and (optionally) move this
issue. Everything else in the feature is written against those five and does
not know which provider it is talking to.

Modelled on :class:`pykantui.sync.base.Backend`, and for the same reason: every
optional capability is a method with a default here, not an attribute callers
are expected to go looking for. A provider that cannot move issues inherits a
``move_issue`` that raises, and the UI never calls it because
:class:`~pykantui.tracker.spec.Capabilities` already told it not to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from typing import Any, ClassVar

from pykantui.api import TTL_ISSUES, JsonHttp, ResponseCache
from pykantui.tracker.errors import UnsupportedError
from pykantui.tracker.models import (
    CommentDraft,
    IssueComponent,
    IssueDraft,
    IssueEdit,
    IssueType,
    RemoteColumn,
    RemoteComment,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.spec import ProviderSpec


class Provider(ABC):
    """One task tracker, reduced to the parts pykantui needs."""

    spec: ClassVar[ProviderSpec]
    issue_cache_labels: ClassVar[tuple[str, ...]] = ("issues",)

    def __init__(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> None:
        """Build a provider from settings and credentials.

        The two are separate arguments rather than one merged mapping so that
        the split survives all the way down: ``config`` is what gets written to
        ``project.json`` in the clear, ``secrets`` is what goes to the private
        auth file. A provider that reads a token out of ``config`` would make
        it impossible for the caller to keep that promise.
        """
        self.config = dict(config)
        self.secrets = dict(secrets)

        #: Built lazily by each provider's ``http`` property, and closed by
        #: :meth:`close`. Declared here so the lifecycle lives in one place
        #: rather than being reimplemented per provider.
        self._http: JsonHttp | None = None

        #: Response cache, handed in by the workspace with a global,
        #: workspace-isolated scope. None means every request goes to the network, which is what
        #: tests and one-shot commands want.
        self.cache: ResponseCache | None = None

        #: Columns fetched this run, so one operation never asks twice.
        #:
        #: Name-mangled deliberately. A base class that claims an ordinary
        #: private name silently breaks any subclass that reaches for the same
        #: one -- which is exactly what happened here, to a provider that kept
        #: its own column list in ``self._columns``. Mangling makes the
        #: collision impossible rather than merely unlikely.
        self.__columns: dict[str, list[RemoteColumn]] = {}

        #: Issue types fetched this run, memoised per project. Mangled for the
        #: same reason as the columns above.
        self.__types: dict[str, list[IssueType]] = {}

        #: Project components fetched this run. Empty for providers without
        #: that concept; Jira supplies the only implementation today.
        self.__components: dict[str, list[IssueComponent]] = {}

        #: How long a cached issue list stays usable. Short by default -- a
        #: burst of commands in one session shares a fetch, but you never look
        #: at a board that is minutes stale. Set to 0 to always refetch, which
        #: is what an explicit ``--refresh`` does.
        self.issue_ttl: float = TTL_ISSUES
        self._issue_refresh_pending = False

    # ---- required --------------------------------------------------------

    @abstractmethod
    def verify(self) -> RemoteUser:
        """Check the credentials and report who they belong to.

        Called by the wizard before it writes anything, so a bad token fails at
        the point the user can still fix it rather than after a project tree
        has been created.
        """

    @abstractmethod
    def list_projects(self) -> list[RemoteProject]:
        """Every project, board or space these credentials can see."""

    def columns(self, project_id: str) -> list[RemoteColumn]:
        """The project's columns, fetched at most once per provider instance.

        Everything inside the app should call this rather than
        :meth:`list_columns`. A sync needs the column list in two places -- to
        resolve the folder a local edit was dragged into, and to place the
        pulled issues -- and asking twice was costing a real request every run.

        Memoised on the instance rather than in the response cache because the
        two answer different questions: this one guarantees consistency *within*
        one operation, where a TTL only limits staleness across operations.
        """
        cached = self.__columns.get(project_id)
        if cached is None:
            cached = self.list_columns(project_id)
            self.__columns[project_id] = cached
        return cached

    def forget_columns(self, project_id: str | None = None) -> None:
        """Drop the memoised columns, after something changed the board shape."""
        if project_id is None:
            self.__columns.clear()
            self.__types.clear()
            self.__components.clear()
        else:
            self.__columns.pop(project_id, None)
            self.__types.pop(project_id, None)
            self.__components.pop(project_id, None)

    @abstractmethod
    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        """Fetch the project's columns, in board order.

        Implemented by each provider; call :meth:`columns` instead.
        """

    @abstractmethod
    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        """Every issue in the project, paging until the provider runs out.

        An iterator rather than a list because these are network calls made a
        page at a time, and the caller writes each issue to disk as it arrives.
        A provider with ten thousand issues should not need all of them in
        memory to write the first file.
        """

    # ---- optional --------------------------------------------------------

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        """Fetch one issue as the tracker currently has it.

        The point is cheap conflict detection. Before pushing a local edit we
        need to know whether the tracker also changed that issue since the last
        sync -- and asking about the one card costs a single small request,
        where re-fetching the project to answer it could be thousands.

        Returns ``None`` where the provider has no single-issue endpoint, which
        the caller reads as "cannot check" and handles by asking rather than by
        assuming it is safe.
        """
        return None

    def iter_comments(self, project_id: str, issue: RemoteIssue) -> Iterator[RemoteComment]:
        """Yield provider comments for one issue in chronological order.

        Providers without a comment endpoint inherit an empty iterator.  The
        separate capability declaration lets callers avoid even invoking it.
        """

        del project_id, issue
        return iter(())

    def comments(
        self,
        project_id: str,
        issue: RemoteIssue,
        *,
        refresh: bool = False,
    ) -> tuple[RemoteComment, ...]:
        """Return one discussion with a provider-neutral, issue-scoped TTL.

        Some trackers expose comments over GraphQL POST or do not provide
        cache validators.  Caching the normalized thread here gives every
        provider the same short-lived disk cache without teaching workspace
        sync which transport an adapter happens to use.
        """
        identity = issue.issue_id.strip() or issue.key.strip()
        cache_key = ""
        cached = None
        if self.cache is not None and identity:
            cache_key = self._comment_cache_key(project_id, identity)
            if refresh:
                self.invalidate_comment_cache(project_id, identity)
            cached = None if refresh else self.cache.get(cache_key)
            if cached is not None and cached.is_fresh(self.issue_ttl):
                body = cached.body
                if isinstance(body, list):
                    try:
                        result = tuple(RemoteComment.model_validate(item) for item in body)
                    except (TypeError, ValueError):
                        pass
                    else:
                        self.cache.hits += 1
                        return result

        result = tuple(self.iter_comments(project_id, issue))
        if self.cache is not None and cache_key:
            self.cache.put(
                cache_key,
                [comment.model_dump(mode="json") for comment in result],
            )
        return result

    def create_comment(
        self,
        project_id: str,
        issue: RemoteIssue,
        draft: CommentDraft,
    ) -> RemoteComment:
        """Create one comment without retrying an ambiguous provider write."""

        del project_id, issue, draft
        raise UnsupportedError(f"{self.spec.label} cannot create comments")

    def issue_types(self, project_id: str) -> list[IssueType]:
        """The kinds of issue this project accepts, fetched at most once.

        Everything in the app should call this rather than
        :meth:`list_issue_types`, for the same reason it calls :meth:`columns`
        rather than :meth:`list_columns`: the answer is needed both to offer
        the choices and to resolve the one that was picked, and asking twice
        costs a real request.
        """
        cached = self.__types.get(project_id)
        if cached is None:
            cached = self.list_issue_types(project_id)
            self.__types[project_id] = cached
        return cached

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        """Fetch the project's issue types. Call :meth:`issue_types` instead.

        Returns ``[]`` where the tracker has no such concept, or will not say
        on this plan. That is deliberately not an error: a tracker without
        types is ordinary, and the caller's correct response is to send no type
        rather than to invent one.
        """
        return []

    def components(self, project_id: str) -> list[IssueComponent]:
        """Return project components at most once per provider instance."""
        cached = self.__components.get(project_id)
        if cached is None:
            cached = self.list_components(project_id)
            self.__components[project_id] = cached
        return cached

    def list_components(self, project_id: str) -> list[IssueComponent]:
        """Fetch project components, or an empty list when unsupported."""
        return []

    def default_issue_type(self, project_id: str) -> IssueType | None:
        """What to use when the user did not choose.

        The project's own default if it declares one, else the first *ordinary*
        type it offers. Level matters: Jira lists Epic first, and defaulting a
        drafted story to an epic silently creates a container at the wrong
        place in the hierarchy -- which reads as working, and is not.

        Never a hardcoded name. "Task" does not exist in every project, and
        Jira reports an unknown type as a permission error on the *project*,
        which sends you looking somewhere else entirely.
        """
        types = [item for item in self.issue_types(project_id) if not item.subtask]
        declared = next((item for item in types if item.default), None)
        if declared is not None:
            return declared

        ordinary = [item for item in types if item.level == 0]
        return next(iter(ordinary or types), None)

    def resolve_issue_type(self, project_id: str, wanted: str) -> IssueType | None:
        """Match what the user typed against what the project actually offers.

        Case-insensitive, because nobody types ``Sub-task`` the way Jira spells
        it. Raises rather than guessing when the name is not on offer: silently
        substituting a different type would create the wrong kind of issue and
        look like it worked.
        """
        if not wanted.strip():
            return self.default_issue_type(project_id)

        types = self.issue_types(project_id)
        if not types:
            # The tracker will not tell us. Honour what was asked for and let
            # it be the judge, rather than refusing on the strength of a list
            # we do not have.
            return IssueType(type_id="", name=wanted.strip())

        needle = wanted.strip().casefold()
        found = next((item for item in types if item.name.casefold() == needle), None)
        if found is not None:
            return found

        offered = ", ".join(item.name for item in types if not item.subtask)
        raise UnsupportedError(
            f"{self.spec.label} has no issue type called {wanted!r} in this project",
            hint=f"It offers: {offered}.",
        )

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        """Create an issue, and return it as the tracker now holds it.

        Returns the *tracker's* version rather than echoing the draft: it
        assigns the key, the id and the url, and may normalise or reject parts
        of what was asked for. The caller writes the file from what came back.

        Only meaningful where ``spec.capabilities.create_issues`` is set.
        """
        raise UnsupportedError(
            f"{self.spec.label} cannot create issues from pykantui",
            hint="Create it in the tracker's own UI, then run a sync.",
        )

    def build_create_payload(self, project_id: str, draft: IssueDraft) -> dict[str, Any]:
        """Translate a neutral draft into this provider's native create body.

        Keeping this translation separate from the HTTP call makes the field
        contract directly testable: unsupported fields cannot accidentally
        leak into a different provider's request.
        """
        raise UnsupportedError(
            f"{self.spec.label} cannot build a create request",
            hint="This provider does not support creating cards.",
        )

    def move_issue(self, issue: RemoteIssue, column: RemoteColumn) -> None:
        """Move an issue into a column.

        Only meaningful where ``spec.capabilities.move_issues`` is set.
        """
        raise UnsupportedError(
            f"{self.spec.label} cannot move issues from pykantui",
            hint="The board shows this backend read-only; edit it in the web UI instead.",
        )

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        """Send a locally edited markdown file back to the tracker.

        One method rather than one per field, because every provider does this
        as a single PUT or PATCH and splitting it would turn one round trip
        into five -- and would make a half-applied edit possible if the third
        call failed.

        The caller is expected to have checked
        :attr:`Capabilities.writable_fields` first; :meth:`reject_unsupported`
        is the guard for when it did not.
        """
        raise UnsupportedError(
            f"{self.spec.label} cannot accept edits from pykantui",
            hint="This tracker is a read-only mirror; edit it in its own web UI.",
        )

    def push_body(self, issue: RemoteIssue, body: str) -> None:
        """Write just the body back. A convenience over :meth:`update_issue`."""
        self.update_issue(issue, IssueEdit(body=body))

    def reject_unsupported(self, edit: IssueEdit) -> None:
        """Fail before sending anything if the edit asks for too much.

        Checked up front so a write is all-or-nothing. Discovering halfway
        through that ``priority`` is not writable, having already changed the
        title, leaves the file and the tracker disagreeing in a way neither
        side can detect afterwards.
        """
        allowed_fields = self.editable_card_fields()
        unsupported = edit.unsupported(allowed_fields)
        if unsupported:
            allowed = ", ".join(allowed_fields) or "nothing"
            raise UnsupportedError(
                f"{self.spec.label} cannot change {', '.join(unsupported)}",
                hint=f"It accepts: {allowed}.",
            )

    def editable_card_fields(self) -> tuple[str, ...]:
        """Card fields available for this configured provider instance."""
        return self.spec.editable_card_fields(self.config)

    def creatable_card_fields(self) -> tuple[str, ...]:
        """Draft fields available for this configured provider instance."""
        return self.spec.creatable_card_fields(self.config)

    def refresh(self) -> None:
        """Make the next issue-list read fresh while retaining structure.

        Once a scoped cache is available, only issue-list entries are removed.
        The next ordinary cached GET therefore reaches the provider and then
        refills the cache for offline/team views.
        """
        self._issue_refresh_pending = True
        self.issue_ttl = 0.0
        self.forget_columns()
        self._apply_issue_refresh()

    def use_cache(self, cache: ResponseCache | None) -> None:
        """Attach a response cache before the client is built.

        Called by the workspace once it knows which project it is syncing, so
        the cache receives that project's isolated global scope. A no-op afterwards, so
        it cannot silently fail to apply to an already-open client.
        """
        self.cache = cache
        if self._http is not None:
            self._http.cache = cache
        self._apply_issue_refresh()

    def _apply_issue_refresh(self) -> None:
        """Apply a refresh requested before or after cache attachment."""
        if not self._issue_refresh_pending or self.cache is None:
            return
        for label in self.issue_cache_labels:
            self.cache.clear_label(label)
        self.issue_ttl = TTL_ISSUES
        self._issue_refresh_pending = False

    def _comment_cache_key(self, project_id: str, issue_id: str) -> str:
        """Build the sole disk-cache key for one normalized discussion."""

        assert self.cache is not None  # noqa: S101 - private helper is cache-bound
        return self.cache.key_for(
            "GET",
            f"comments/{project_id}/{issue_id}",
            None,
            "comments",
        )

    def invalidate_comment_cache(self, project_id: str, issue_id: str) -> None:
        """Drop one cached discussion after an explicit refresh or append."""

        identity = issue_id.strip()
        if self.cache is not None and identity:
            self.cache.discard(self._comment_cache_key(project_id, identity))

    def close(self) -> None:
        """Release the pooled connections. Safe to call more than once.

        Every provider holds its client on ``_http`` and builds it lazily, so
        the one implementation here covers all of them; a provider that never
        connected has nothing to close.
        """
        if self._http is not None:
            self._http.close()
            self._http = None

    # ---- helpers for subclasses -----------------------------------------

    def required(self, name: str) -> str:
        """Fetch a required setting, or fail with a message naming the field.

        ``KeyError: 'workspace'`` from three frames down is not a usable error
        for someone who mistyped a wizard answer.
        """
        value = str(self.config.get(name) or self.secrets.get(name) or "").strip()
        if not value:
            field = self.spec.field_named(name)
            label = field.label if field else name
            raise UnsupportedError(
                f"{self.spec.label} needs {label}",
                hint=f"Set {name} in the project config, or pass {field.cli_flag if field else '--' + name}.",
            )
        return value

    def optional(self, name: str, default: str = "") -> str:
        return str(self.config.get(name) or self.secrets.get(name) or default).strip()

    def __enter__(self) -> Provider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
