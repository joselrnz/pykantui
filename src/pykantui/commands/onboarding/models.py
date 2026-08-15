"""Typed values shared by provider-neutral onboarding steps."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class CredentialSource(StrEnum):
    """Where a resolved provider field came from."""

    ARGUMENT = "command line"
    ENVIRONMENT = "environment"
    SAVED = "private store"
    DEFAULT = "provider default"
    ENTERED = "entered now"


class CredentialPersistence(StrEnum):
    """How credentials remain available after setup finishes."""

    PRIVATE_STORE = "private_store"
    ENVIRONMENT = "environment"
    SESSION = "session"


@dataclass(frozen=True)
class CredentialSetup:
    """Resolved setup values without exposing secrets through ``repr``."""

    config: Mapping[str, object]
    _secrets: Mapping[str, str] = field(repr=False)
    sources: Mapping[str, CredentialSource]
    persistence: CredentialPersistence = CredentialPersistence.SESSION

    @property
    def secrets(self) -> dict[str, str]:
        """Return a provider-ready copy of the secret values."""

        return dict(self._secrets)

    @property
    def should_save(self) -> bool:
        """Whether setup must write a new private credential record."""

        return self.persistence is CredentialPersistence.PRIVATE_STORE and any(
            source is not CredentialSource.SAVED
            for name, source in self.sources.items()
            if name in self._secrets
        )
