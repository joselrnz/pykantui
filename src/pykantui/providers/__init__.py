"""One subpackage per task tracker. Nothing else lives here.

Each subpackage contains only that tracker's spec and its mapping from the tracker's
own wire format onto :mod:`pykantui.tracker`'s neutral objects. The contract
they implement, the registry that finds them, the HTTP client they share and
the markup converters they use all live in :mod:`pykantui.tracker` -- so this
package stays a list of integrations rather than a mixture of integrations and
machinery.

Every bundled provider is marked verified only after its read and write paths
have been exercised against a live workspace. The wizard reads that state from
each provider's own spec, so adding or validating an integration does not
require maintaining a second list here.

The nine differ in nearly every dimension, which is the useful part -- if the
contract absorbs all of them, it will absorb the next one:

===========  ==========  ==========================  ==========================
Tracker      Protocol    Auth                        Columns come from
===========  ==========  ==========================  ==========================
Jira         REST        basic, email + token        board config or statuses
Plane        REST        ``X-API-Key``               state groups
Trello       REST        key + token in the query    lists
Monday.com   GraphQL     bare ``Authorization``      status column labels
Linear       GraphQL     bare ``Authorization``      workflow state types
GitHub       REST        ``Bearer``                  status labels, or open/closed
Asana        REST        ``Bearer``                  sections
ClickUp      REST        bare ``Authorization``      list statuses
Shortcut     REST        ``Shortcut-Token``          workflow states
===========  ==========  ==========================  ==========================

Four of them put the token straight in ``Authorization`` with no scheme word,
which is the single most common thing to get wrong.

Adding a tracker means adding one subpackage here; discovery is automatic.
Adding one *without* touching this package at all means shipping it as a
separate distribution advertising the
``pykantui.providers`` entry point.

Nothing imports these modules directly. Ask the registry by name::

    from pykantui.tracker import build, specs

    provider = build("jira", config, secrets)

which keeps a tracker needing an optional dependency from being imported by
someone who does not use it.
"""

from __future__ import annotations


def builtin_providers() -> tuple[str, ...]:
    """The trackers in this package, found by scanning it.

    Derived rather than listed. A hand-written tuple beside a directory of
    modules is a second source of truth, and it goes stale in the one direction
    that matters: a provider that exists but is not offered.

    Scanning does not import anything, so this stays free of every provider's
    dependencies -- which is the whole reason this module has no imports of its
    own beyond the standard library.
    """
    import pkgutil  # noqa: PLC0415 - kept local so the module stays import-light

    return tuple(sorted(module.name for module in pkgutil.iter_modules(__path__) if not module.name.startswith("_")))


def verified_providers() -> tuple[str, ...]:
    """The trackers that have been run against a real instance.

    Read off each provider's own ``spec.verified`` rather than kept in a list
    here, so that testing one is a one-line change in that provider and not a
    change in two files -- the second of which would be forgotten.
    """
    from pykantui.tracker import specs  # noqa: PLC0415 - avoids an import cycle at module load

    return tuple(sorted(spec.name for spec in specs() if spec.verified))


__all__ = ["builtin_providers", "verified_providers"]
