"""Origin-bound provider credentials and connection-setting validation.

Workspace configuration is intentionally committable.  Credentials are not,
so a workspace must never be able to choose an arbitrary HTTP origin and have
the user's globally saved token attached to it.  This module owns that trust
boundary: secrets are stored under both provider name and canonical origin.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pykantui.config import env as dotenv
from pykantui.config.paths import auth_path, write_text_atomic
from pykantui.tracker import get
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.spec import FieldKind, ProviderField

AUTH_SCHEMA = 2
_UNBOUND_SCOPE = "unbound"


class AuthDocument(BaseModel):
    """Private credential store, grouped by provider and trusted scope."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[2] = Field(default=2, alias="schema")
    providers: dict[str, dict[str, dict[str, str]]] = Field(default_factory=dict)


def canonical_origin(value: str, *, label: str = "provider URL") -> str:
    """Return a stable HTTPS origin, rejecting ambiguous or unsafe URLs."""

    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as error:
        raise ProviderError(f"{label} is not a valid URL", hint=str(error)) from error

    if parsed.scheme.lower() != "https":
        raise ProviderError(f"{label} must use HTTPS", hint="Use an https:// URL before entering credentials.")
    if not parsed.hostname:
        raise ProviderError(f"{label} needs a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderError(f"{label} must not contain a username or password")
    if parsed.query or parsed.fragment:
        raise ProviderError(f"{label} must not contain a query or fragment")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ProviderError(f"{label} has an invalid hostname") from error
    if ":" in hostname:
        hostname = f"[{hostname}]"
    suffix = f":{port}" if port is not None and port != 443 else ""
    return f"https://{hostname}{suffix}"


def validate_provider_config(provider: str, config: Mapping[str, object]) -> dict[str, Any]:
    """Reject undeclared settings and validate every provider URL."""

    spec = get(provider).spec
    allowed = {field.name for field in spec.all_fields() if not field.secret}
    unknown = sorted(set(config) - allowed)
    if unknown:
        noun = "setting" if len(unknown) == 1 else "settings"
        raise ProviderError(
            f"{provider} workspace has unknown {noun}: {', '.join(unknown)}",
            hint="Do not trust this workspace until its project.json has been reviewed.",
        )

    validated = dict(config)
    for field in spec.all_fields():
        if field.kind is not FieldKind.URL:
            continue
        value = str(validated.get(field.name, "") or field.default).strip()
        if value:
            canonical_origin(value, label=field.label)
    return validated


def credential_scope(provider: str, config: Mapping[str, object]) -> str:
    """The exact origin a provider's credentials are trusted for."""

    spec = get(provider).spec
    field = _credential_url_field(provider)
    if field is None:
        return f"provider:{spec.name}"
    value = str(config.get(field.name, "") or field.default).strip()
    if not value:
        raise ProviderError(f"{provider} needs {field.name} before credentials can be loaded")
    return canonical_origin(value, label=field.label)


def environment_trusts_scope(provider: str, config: Mapping[str, object]) -> bool:
    """Whether environment credentials may be attached to this configuration."""

    scope = credential_scope(provider, config)
    default = _default_scope(provider)
    if default is not None and scope == default:
        return True

    field = _credential_url_field(provider)
    if field is None:
        return True
    environment_url = field.from_env()
    return bool(environment_url and canonical_origin(environment_url, label=field.label) == scope)


def environment_value_trusts_scope(
    provider: str,
    config: Mapping[str, object],
    field: ProviderField,
) -> bool:
    """Whether one environment credential can be sent to this provider scope.

    A workspace ``.env`` is project-controlled input.  If it supplied a
    provider URL, only credentials from that same file may accompany it.
    Separately exported credentials remain available for official/default
    origins and for origins the process explicitly exported itself.
    """

    if not environment_trusts_scope(provider, config):
        return False
    url_field = _credential_url_field(provider)
    if url_field is None:
        return True
    url_name, url_value = _environment_value(url_field)
    if not url_name or not dotenv.supplied_by_file(url_name, url_value):
        return True
    credential_name, credential_value = _environment_value(field)
    return bool(credential_name and dotenv.supplied_by_file(credential_name, credential_value))


def load_secrets(provider: str, config: Mapping[str, object] | None = None) -> dict[str, str]:
    """Load credentials only from the provider's explicitly trusted scope."""

    raw = _read_raw_document()
    document, legacy = _decode_document(raw)

    if config is None:
        scoped = document.providers.get(provider, {})
        usable = [value for key, value in scoped.items() if key != _UNBOUND_SCOPE]
        if len(usable) == 1:
            return dict(usable[0])
        if not usable and _UNBOUND_SCOPE in scoped:
            return dict(scoped[_UNBOUND_SCOPE])
        return dict(legacy.get(provider, {}))

    validated = validate_provider_config(provider, config)
    scope = credential_scope(provider, validated)
    scoped = document.providers.get(provider, {})
    if scope in scoped:
        return dict(scoped[scope])

    # A legacy token had no origin.  It is safe to inherit only for a fixed,
    # official default; dynamic Jira/enterprise origins must be re-approved.
    if scope == _default_scope(provider):
        if _UNBOUND_SCOPE in scoped:
            return dict(scoped[_UNBOUND_SCOPE])
        return dict(legacy.get(provider, {}))
    return {}


def save_secrets(
    provider: str,
    secrets: Mapping[str, str],
    *,
    config: Mapping[str, object] | None = None,
) -> None:
    """Merge secrets under the provider and exact trusted origin."""

    if _provider_exists(provider):
        validated = validate_provider_config(provider, config or {})
        scope = credential_scope(provider, validated)
        declared = {field.name for field in get(provider).spec.auth_fields if field.secret}
    elif config:
        raise ProviderError(f"cannot validate connection settings for unknown provider {provider!r}")
    else:
        # Preserve credentials for a temporarily uninstalled third-party
        # provider.  They cannot be consumed until that provider is present.
        scope = f"provider:{provider}"
        declared = set(secrets)
    document, legacy = _decode_document(_read_raw_document())
    _preserve_legacy(document, legacy)

    cleaned = {name: str(value) for name, value in secrets.items() if name in declared and value}
    provider_scopes = document.providers.setdefault(provider, {})
    merged = dict(provider_scopes.get(scope, {}))
    merged.update(cleaned)
    provider_scopes[scope] = merged

    write_text_atomic(auth_path(), document.model_dump_json(indent=2, by_alias=True), private=True)


def resolve_fields(provider: str, supplied: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve provider fields while keeping config and secrets separated."""

    spec = get(provider).spec
    config: dict[str, Any] = {}
    environment_config_fields: set[str] = set()
    for field in spec.all_fields():
        if field.secret:
            continue
        explicit = supplied.get(field.name, "")
        environment = field.from_env()
        value = explicit or environment or field.default
        if value:
            config[field.name] = value
            if not explicit and environment:
                environment_config_fields.add(field.name)
    config = validate_provider_config(provider, config)
    for field in spec.auth_fields:
        if field.secret or field.kind is FieldKind.URL or field.name not in environment_config_fields:
            continue
        if not environment_value_trusts_scope(provider, config, field):
            config.pop(field.name, None)

    # Dynamic providers (notably Jira) do not have a trustworthy origin until
    # the setup wizard asks for one.  At that point there is deliberately no
    # saved credential scope to consult yet.
    saved = load_secrets(provider, config) if _credential_scope_is_ready(provider, config) else {}
    secrets: dict[str, str] = {}
    for field in spec.all_fields():
        if not field.secret:
            continue
        explicit = supplied.get(field.name, "")
        environment = field.from_env()
        if explicit:
            value = explicit
        elif environment and environment_value_trusts_scope(provider, config, field):
            value = environment
        else:
            value = saved.get(field.name, "") or field.default
        if value:
            secrets[field.name] = value
    return config, secrets


def _environment_value(field: ProviderField) -> tuple[str, str]:
    """Return the first populated environment name and its stripped value."""

    for name in field.env_vars:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return "", ""


def _credential_url_field(provider: str) -> ProviderField | None:
    return next((field for field in get(provider).spec.auth_fields if field.kind is FieldKind.URL), None)


def _credential_scope_is_ready(provider: str, config: Mapping[str, object]) -> bool:
    field = _credential_url_field(provider)
    return field is None or bool(str(config.get(field.name, "") or field.default).strip())


def _default_scope(provider: str) -> str | None:
    field = _credential_url_field(provider)
    if field is None:
        return f"provider:{get(provider).spec.name}"
    return canonical_origin(field.default, label=field.label) if field.default else None


def _read_raw_document() -> object:
    try:
        return json.loads(auth_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _decode_document(raw: object) -> tuple[AuthDocument, dict[str, dict[str, str]]]:
    if isinstance(raw, dict) and raw.get("schema") == AUTH_SCHEMA:
        try:
            return AuthDocument.model_validate(raw), {}
        except ValidationError:
            return AuthDocument(), {}

    legacy: dict[str, dict[str, str]] = {}
    if isinstance(raw, dict):
        for provider, values in raw.items():
            if not isinstance(values, dict):
                continue
            legacy[str(provider)] = {str(name): str(value) for name, value in values.items() if value}
        if raw.get("jira_token"):
            legacy.setdefault("jira", {}).setdefault("token", str(raw["jira_token"]))
    return AuthDocument(), legacy


def _preserve_legacy(document: AuthDocument, legacy: Mapping[str, dict[str, str]]) -> None:
    for provider, secrets in legacy.items():
        scopes = document.providers.setdefault(provider, {})
        scope = _default_scope(provider) if _provider_exists(provider) else None
        scopes.setdefault(scope or _UNBOUND_SCOPE, dict(secrets))


def _provider_exists(provider: str) -> bool:
    try:
        get(provider)
    except ProviderError:
        return False
    return True
