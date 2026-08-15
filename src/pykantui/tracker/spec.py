"""What a provider declares about itself.

The wizard, the CLI parser and the docs all read a provider's ``spec`` rather
than hard-coding what Jira needs versus what Trello needs. Adding a provider is
then a module and a registration, with no edit to the UI, the argument parser
or the help text -- which is the whole point of the registry.

The field class here is :class:`ProviderField`, not ``Field``. Pydantic already
owns that name and these modules are full of pydantic models; two different
``Field``\\ s in one file is a bug waiting for a tired afternoon.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pykantui.core.work_items import WorkItemColumn, available_work_item_columns
from pykantui.tracker.fields import CardFieldName, CardFieldSpec, card_schema
from pykantui.tracker.filter_fields import (
    FilterFieldSpec,
    ProviderFilterLabels,
    filter_schema,
)


class FieldKind(StrEnum):
    """How the wizard should render a field, and how the CLI should parse it."""

    TEXT = "text"
    SECRET = "secret"
    URL = "url"
    INTEGER = "integer"
    BOOLEAN = "boolean"

    #: Values come from the provider at runtime -- the project list, once the
    #: credentials are in. Rendered as a picker rather than a free-text box.
    CHOICE = "choice"


class CredentialSetupKind(StrEnum):
    """How a provider issues the credentials linked from the setup wizard.

    Most providers offer credentials for the signed-in user and do not need an
    OAuth application. A provider-owned application flow is declared
    separately so the wizard never hides a registration requirement behind a
    vague ``Authenticate`` action.
    """

    GENERIC = "generic"
    PERSONAL = "personal"
    PROVIDER_APPLICATION = "provider-application"


class SpecModel(BaseModel):
    """Specs are declared once at import and never mutated."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProviderField(SpecModel):
    """One question the wizard asks, and one flag the CLI accepts."""

    name: str
    label: str
    kind: FieldKind = FieldKind.TEXT
    required: bool = True
    default: str = ""
    help: str = ""

    #: Placeholder shown in the wizard. A concrete example beats a description:
    #: "https://acme.atlassian.net" teaches the format in one glance.
    placeholder: str = ""

    #: Where the value may come from instead of being typed. Checked in order,
    #: so a token already in the environment means the wizard never asks.
    env_vars: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _usable_as_a_flag(cls, value: str) -> str:
        """A field name has to survive becoming both a CLI flag and a JSON key."""
        if not value or not value.replace("_", "").isalnum():
            raise ValueError(f"field name {value!r} must be alphanumeric with underscores")
        return value

    @property
    def secret(self) -> bool:
        """Secrets go to the private auth file, never to ``project.json``.

        Derived from the kind rather than declared separately, so the two
        cannot drift apart and leak a token into a world-readable file.
        """
        return self.kind is FieldKind.SECRET

    @property
    def cli_flag(self) -> str:
        return f"--{self.name.replace('_', '-')}"

    def from_env(self) -> str:
        """The first of this field's environment variables that is actually set."""
        for name in self.env_vars:
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return ""


class Capabilities(SpecModel):
    """What this provider can actually do.

    The UI asks these rather than checking the provider's name, so a read-only
    provider simply has no edit action on the menu instead of offering one that
    fails after you pick it.
    """

    #: Can issues be moved between columns from the board?
    move_issues: bool = False

    #: Does the provider keep a client-side order within a column?
    reorder_issues: bool = False

    #: Can new issues be created through the app?
    create_issues: bool = False

    #: Which fields a local markdown edit can push back, from
    #: :data:`~pykantui.tracker.models.EDITABLE_FIELDS`. Empty means the
    #: tracker is a read-only mirror.
    #:
    #: A list rather than a flag because the trackers genuinely differ: Jira
    #: takes a summary and a description but moves status only through a
    #: transition, Trello takes almost everything, and Monday needs a typed
    #: mutation per column. Provider edit mode shows only fields that can be
    #: sent instead of accepting an edit it will have to throw away.
    writable_fields: tuple[str, ...] = ()

    #: The provider's query language, where showing a query box makes sense.
    query_language: str = ""

    #: Does it have a separate backlog, distinct from the board? A Jira
    #: team-managed board does not -- see the plan, section 0b.
    backlog: bool = False

    #: Can discussion comments be read for a card? Kept separate from create
    #: because read-only credentials and provider plans are common.
    read_comments: bool = False

    #: Can a new append-only comment be created during confirmed Sync?
    create_comments: bool = False


class ProviderSpec(SpecModel):
    """Everything the app knows about a provider before it connects to one."""

    name: str
    label: str
    description: str = ""

    #: Credentials. Go to the private auth file, keyed by provider name.
    auth_fields: tuple[ProviderField, ...] = ()

    #: Connection and scope settings. Go to ``project.json``, in the clear.
    config_fields: tuple[ProviderField, ...] = ()

    capabilities: Capabilities = Field(default_factory=Capabilities)

    #: Complete provider-owned description of the normalised card fields.
    #: Unsupported fields remain present with both operation flags false; this
    #: makes omissions visible and keeps provider-specific wire keys together.
    card_fields: tuple[CardFieldSpec, ...] = Field(default_factory=lambda: card_schema({}))

    #: Read-only table values outside the editable card-field contract.
    #: Reporter and created timestamps are provider response data, not fields
    #: the editor may send back, so providers declare them separately.
    table_fields: tuple[WorkItemColumn, ...] = ()

    #: Tracker terminology for the expanded filter bar. Availability of
    #: assignee/type/priority/labels is derived from ``card_fields`` so the
    #: editor and filter bar cannot disagree about what the provider has.
    filter_labels: ProviderFilterLabels = Field(default_factory=ProviderFilterLabels)

    #: Where to go to create a token. Shown in the wizard next to the secret
    #: field, because "paste your API token" without a link is a dead end.
    token_url: str = ""

    #: What the linked credential page asks the user to create. This drives
    #: explicit wizard copy; it does not change how credentials are stored.
    credential_setup: CredentialSetupKind = CredentialSetupKind.GENERIC

    #: False for providers registered but known not to work yet, so one can be
    #: developed in the open without appearing in the wizard.
    available: bool = True

    #: Whether this provider has been exercised against a real instance, as
    #: opposed to written from published documentation. Declared here so the
    #: wizard can flag it, rather than kept in a list somewhere that has to be
    #: remembered when a provider is finally tested.
    verified: bool = False

    @field_validator("name")
    @classmethod
    def _lowercase_name(cls, value: str) -> str:
        """Names are looked up case-insensitively; normalise once, here."""
        return value.strip().lower()

    def filter_fields(self, config: Mapping[str, object] | None = None) -> tuple[FilterFieldSpec, ...]:
        """Filter boxes available for this provider and board configuration."""

        declared = filter_schema(
            self.card_fields,
            self.filter_labels,
            query_language=self.capabilities.query_language,
        )
        return tuple(field for field in declared if field.available(config))

    def available_table_fields(
        self,
        config: Mapping[str, object] | None = None,
    ) -> frozenset[WorkItemColumn]:
        """Rows/Split columns supplied by this provider configuration."""
        return available_work_item_columns(
            self.card_fields,
            config,
            extra=self.table_fields,
        )

    @field_validator("capabilities")
    @classmethod
    def _writable_fields_are_real(cls, value: Capabilities) -> Capabilities:
        """Reject a writable field name nothing downstream can map.

        A typo here would fail silently and much later, as an edit the editor
        offers and the provider then refuses.
        """
        from pykantui.tracker.models import EDITABLE_FIELDS  # noqa: PLC0415 - avoids an import cycle

        unknown = [name for name in value.writable_fields if name not in EDITABLE_FIELDS]
        if unknown:
            raise ValueError(f"writable_fields must come from {', '.join(EDITABLE_FIELDS)}; got {', '.join(unknown)}")
        return value

    @model_validator(mode="after")
    def _card_fields_are_complete_and_match_capabilities(self) -> ProviderSpec:
        names = tuple(field.name for field in self.card_fields)
        expected = tuple(CardFieldName)
        if names != expected:
            raise ValueError("card_fields must contain every normalised field once, in canonical order")
        declared = {field.name.value for field in self.card_fields if field.editable}
        if declared and declared != set(self.capabilities.writable_fields):
            raise ValueError("editable card_fields must match capabilities.writable_fields")
        for field in self.card_fields:
            if (field.editable or field.creatable) and not field.provider_key:
                raise ValueError(f"supported card field {field.name} needs a provider_key")
        return self

    @field_validator("config_fields")
    @classmethod
    def _no_secrets_in_config(cls, value: tuple[ProviderField, ...]) -> tuple[ProviderField, ...]:
        """Keep secrets out of the config half.

        ``config_fields`` is written to ``project.json`` in the clear and that
        file is expected to be committable. A secret declared on the wrong side
        of the split would be written there, so this is a hard error at import
        rather than a surprise in someone's git history.
        """
        leaked = [item.name for item in value if item.secret]
        if leaked:
            raise ValueError(f"secret fields belong in auth_fields, not config_fields: {', '.join(leaked)}")
        return value

    def all_fields(self) -> tuple[ProviderField, ...]:
        return self.auth_fields + self.config_fields

    def field_named(self, name: str) -> ProviderField | None:
        return next((item for item in self.all_fields() if item.name == name), None)

    def required_fields(self) -> tuple[ProviderField, ...]:
        return tuple(item for item in self.all_fields() if item.required)

    def card_field(self, name: CardFieldName | str) -> CardFieldSpec:
        wanted = CardFieldName(name)
        return next(field for field in self.card_fields if field.name is wanted)

    def editable_card_fields(self, config: Mapping[str, object] | None = None) -> tuple[str, ...]:
        declared = tuple(field.name.value for field in self.card_fields if field.editable and field.available(config))
        # Compatibility for third-party providers written before card schemas:
        # their existing capability declaration remains authoritative.
        return declared or self.capabilities.writable_fields

    def creatable_card_fields(self, config: Mapping[str, object] | None = None) -> tuple[str, ...]:
        declared = tuple(field.name.value for field in self.card_fields if field.creatable and field.available(config))
        return declared or (self.capabilities.writable_fields if self.capabilities.create_issues else ())
