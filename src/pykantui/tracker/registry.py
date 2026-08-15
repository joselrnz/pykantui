"""Which providers exist, and how a new one gets added.

Three sources, all feeding one list:

1. **Built-ins** -- every module in :data:`BUILTIN_PACKAGE`, discovered by
   scanning the package rather than by a hand-written list. Dropping a file in
   there is the whole job.
2. **Entry points** -- anything advertising ``pykantui.providers``, so a new
   integration ships as its own package and appears in the wizard with no edit
   here.
3. **Direct registration** -- :func:`register`, for tests and for plugins that
   are already imported.

Registration stores a *factory*, never a class. Importing this module must not
import an SDK for a provider nobody is using; the module behind a provider is
imported the first time someone actually asks for it.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from types import ModuleType
from typing import TYPE_CHECKING

from pykantui.tracker.errors import ProviderError
from pykantui.tracker.spec import ProviderSpec

if TYPE_CHECKING:
    from pykantui.tracker.base import Provider

#: Entry-point group third-party providers advertise themselves under.
ENTRY_POINT_GROUP = "pykantui.providers"

#: Where the built-in providers live. One subpackage per tracker, named after it.
BUILTIN_PACKAGE = "pykantui.providers"

_registered: dict[str, Callable[[], type[Provider]]] = {}
_entry_points_loaded = False


def register(name: str, factory: Callable[[], type[Provider]], *, replace: bool = False) -> None:
    """Add a provider under ``name``.

    Refuses to shadow an existing name unless asked to. A plugin silently
    replacing the built-in Jira provider is the kind of thing that should be
    deliberate.
    """
    key = name.strip().lower()
    if not key:
        raise ValueError("a provider needs a name")
    if key in _registered and not replace:
        raise ValueError(f"provider {key!r} is already registered; pass replace=True to override it")
    _registered[key] = factory


def unregister(name: str) -> None:
    """Drop a registration. Mainly for tests, which must not leak into each other."""
    _registered.pop(name.strip().lower(), None)


def get(name: str) -> type[Provider]:
    """The provider class registered under ``name``.

    Raises :class:`~pykantui.tracker.errors.ProviderError` listing what *is*
    available, because the common cause of getting here is a typo.
    """
    key = name.strip().lower()
    _load_entry_points()

    factory = _registered.get(key)
    if factory is None and key in builtin_names():
        factory = _builtin_factory(key)
    if factory is None:
        known = ", ".join(sorted(names())) or "none"
        raise ProviderError(f"no provider named {name!r}", hint=f"Available: {known}")

    try:
        return factory()
    except ImportError as error:
        raise ProviderError(
            f"provider {key!r} could not be loaded: {error}",
            hint=f"It may need an optional dependency: pip install 'pykantui[{key}]'",
        ) from error


def names() -> list[str]:
    """Every registered provider name, built-in and plugin alike."""
    _load_entry_points()
    return sorted(set(builtin_names()) | set(_registered))


def specs(*, available_only: bool = True) -> list[ProviderSpec]:
    """Every provider's spec, for the wizard and for ``kbn init --list-types``.

    A provider that fails to import is skipped rather than taking the list down
    with it -- one broken plugin must not make the wizard unusable.
    """
    found: list[ProviderSpec] = []
    for name in names():
        try:
            spec = get(name).spec
        except ProviderError:
            continue
        if available_only and not spec.available:
            continue
        found.append(spec)
    return sorted(found, key=lambda item: item.label.lower())


def build(name: str, config: dict[str, object], secrets: dict[str, str]) -> Provider:
    """Instantiate a provider by name."""
    return get(name)(config, secrets)


def builtin_names() -> tuple[str, ...]:
    """The trackers shipped in :data:`BUILTIN_PACKAGE`, found by scanning it.

    Scanned rather than listed, so adding ``notion/`` to that package is the
    entire job -- no registry entry, no manifest, nothing to keep in agreement.
    A hand-written list is a second source of truth, and the failure mode is a
    provider that exists but cannot be selected.

    ``pkgutil.iter_modules`` reads directory entries and does **not** import
    anything, so this stays cheap and keeps a provider's dependencies unloaded
    until someone actually asks for it.
    """
    try:
        package = importlib.import_module(BUILTIN_PACKAGE)
        paths = list(getattr(package, "__path__", []))
    except ImportError:  # pragma: no cover - the package ships with us
        return ()
    return tuple(sorted(module.name for module in pkgutil.iter_modules(paths) if not module.name.startswith("_")))


def _builtin_factory(name: str) -> Callable[[], type[Provider]]:
    def factory() -> type[Provider]:
        module = importlib.import_module(f"{BUILTIN_PACKAGE}.{name}")
        found = _provider_class_in(module)
        if found is None:
            raise ProviderError(
                f"{module.__name__} defines no Provider subclass",
                hint="A provider module must define exactly one class deriving from Provider.",
            )
        return found

    return factory


def _provider_class_in(module: ModuleType) -> type[Provider] | None:
    """The Provider subclass a module defines.

    Found by inspection rather than by a naming convention, so a module is free
    to call its class whatever reads best. Provider packages re-export the
    class defined by their ``provider`` module, so matches stay inside the
    package namespace rather than requiring the package itself to define it.
    """
    from pykantui.tracker.base import Provider as Base  # noqa: PLC0415 - avoids an import cycle

    for value in vars(module).values():
        if (
            isinstance(value, type)
            and issubclass(value, Base)
            and value is not Base
            and (value.__module__ == module.__name__ or value.__module__.startswith(f"{module.__name__}."))
        ):
            return value
    return None


def _load_entry_points() -> None:
    """Discover plugin providers once per process."""
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    _entry_points_loaded = True

    for entry in _entry_points(ENTRY_POINT_GROUP):
        name = entry.name.strip().lower()
        if name in _registered:
            continue
        _registered[name] = _entry_factory(entry)


def _entry_factory(entry: EntryPoint) -> Callable[[], type[Provider]]:
    def factory() -> type[Provider]:
        loaded: type[Provider] = entry.load()
        return loaded

    return factory


def _entry_points(group: str) -> Iterable[EntryPoint]:
    """Entry points for ``group``, tolerating a hostile environment.

    A frozen executable may have no metadata at all, and a single broken
    third-party distribution can make the whole scan raise. Neither is a reason
    for the built-in providers to become unreachable, so this swallows
    everything and returns nothing rather than propagating.
    """
    try:
        return list(entry_points(group=group))
    except Exception:  # noqa: BLE001 - see docstring; never fatal
        return []
