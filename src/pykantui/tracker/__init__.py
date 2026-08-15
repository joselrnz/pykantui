"""The contract every task tracker implements, and the machinery it shares.

This package is the framework; :mod:`pykantui.providers` holds the trackers
themselves. The split is the point -- one file per tracker over there, and
everything common exactly once over here:

* :mod:`~pykantui.tracker.base` -- the :class:`Provider` contract.
* :mod:`~pykantui.tracker.spec` -- what a provider declares before connecting.
* :mod:`~pykantui.tracker.registry` -- finding providers, including plugins.
* :mod:`~pykantui.tracker.models` -- the neutral objects providers return.
* :mod:`pykantui.api` -- one pooled httpx client, caching and paging shapes.
* :mod:`~pykantui.tracker.markup` -- ADF, HTML and wiki markup into markdown.

A provider answers five questions: who am I, what projects are there, what
columns does one have, what issues does it hold, and optionally -- move this
issue. Everything above this layer is written against those five and never
learns which tracker is underneath::

    from pykantui.tracker import build, specs

    for spec in specs():
        print(spec.name, spec.label)

    with build("jira", config, secrets) as provider:
        user = provider.verify()
        for issue in provider.iter_issues("JPT"):
            ...

Ask the registry by name rather than importing a provider module, so that a
tracker needing an optional dependency is imported only when it is used.
"""

from __future__ import annotations

from pykantui.tracker.base import Provider
from pykantui.tracker.errors import (
    AuthError,
    NotFoundError,
    ProviderError,
    RateLimitError,
    TransportError,
    UnsupportedError,
)
from pykantui.tracker.models import (
    COLUMN_BACKLOG,
    COLUMN_CANCELLED,
    COLUMN_DONE,
    COLUMN_GROUPS,
    COLUMN_REVIEW,
    COLUMN_STARTED,
    COLUMN_TODO,
    COLUMN_UNKNOWN,
    EDITABLE_FIELDS,
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
from pykantui.tracker.registry import build, get, names, register, specs, unregister
from pykantui.tracker.spec import (
    Capabilities,
    CredentialSetupKind,
    FieldKind,
    ProviderField,
    ProviderSpec,
)

__all__ = [
    # contract
    "Provider",
    "ProviderSpec",
    "ProviderField",
    "FieldKind",
    "CredentialSetupKind",
    "Capabilities",
    # registry
    "build",
    "get",
    "names",
    "register",
    "specs",
    "unregister",
    # data
    "RemoteUser",
    "RemoteProject",
    "RemoteColumn",
    "RemoteIssue",
    "RemoteComment",
    "CommentDraft",
    "IssueEdit",
    "IssueComponent",
    "IssueDraft",
    "IssueType",
    "EDITABLE_FIELDS",
    "COLUMN_GROUPS",
    "COLUMN_BACKLOG",
    "COLUMN_TODO",
    "COLUMN_STARTED",
    "COLUMN_REVIEW",
    "COLUMN_DONE",
    "COLUMN_CANCELLED",
    "COLUMN_UNKNOWN",
    # errors
    "ProviderError",
    "AuthError",
    "NotFoundError",
    "RateLimitError",
    "TransportError",
    "UnsupportedError",
]
