"""Secure credential discovery and persistence choices for ``kbn init``."""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import replace

from pykantui.commands.onboarding.models import CredentialPersistence, CredentialSetup, CredentialSource
from pykantui.i18n import translate as _
from pykantui.pages.chooser import Choice
from pykantui.pages.init_wizard import InitWizardApp
from pykantui.tracker import get
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.spec import CredentialSetupKind, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace.credentials import environment_value_trusts_scope, load_secrets, resolve_fields
from pykantui.workspace.project import missing_required


async def collect_credentials(
    wizard: InitWizardApp,
    provider_name: str,
    supplied: Mapping[str, str],
    *,
    open_url: Callable[[str], bool] | None = None,
    force_entry: bool = False,
) -> CredentialSetup:
    """Discover or collect required fields without saving anything.

    Values are resolved in the existing precedence order: explicit argument,
    environment, origin-bound private store, then provider default.  The TUI
    names the source but never renders a credential value.
    """

    spec = get(provider_name).spec
    entered: dict[str, str] = {}
    effective_supplied = dict(supplied)
    if force_entry:
        for provider_field in spec.auth_fields:
            effective_supplied.pop(provider_field.name, None)
    config, secrets = resolve_fields(provider_name, effective_supplied)
    if force_entry:
        for provider_field in spec.auth_fields:
            if provider_field.secret:
                secrets.pop(provider_field.name, None)
            elif provider_field.required:
                config.pop(provider_field.name, None)
    missing_fields = _missing_non_choice(spec, config, secrets)
    if not missing_fields:
        sources = _field_sources(spec, supplied, entered, config, secrets)
        wizard.note(
            _("Detected {provider} credentials: {sources}").format(
                provider=spec.label,
                sources=_source_description(spec, sources),
            )
        )
        return CredentialSetup(dict(config), dict(secrets), sources)
    else:
        action = await wizard.choose(
            _authentication_choices(spec),
            title=_("Set up {provider} credentials").format(provider=spec.label),
            filter_hint=_("choose how to provide credentials"),
        )

    if action == "authenticate":
        await _open_authentication_page(wizard, spec, open_url or webbrowser.open)

    for provider_field in spec.required_fields():
        if provider_field.kind is FieldKind.CHOICE or provider_field.secret:
            continue
        if config.get(provider_field.name):
            continue
        entered[provider_field.name] = await _prompt_field(wizard, provider_name, provider_field, secrets)
        config[provider_field.name] = entered[provider_field.name]

    # A dynamic origin (notably Jira) may unlock an existing origin-bound
    # token only after its URL has been entered. Re-resolve at that point.
    combined = {**effective_supplied, **entered}
    config, discovered_secrets = resolve_fields(provider_name, combined)
    secrets = discovered_secrets
    if force_entry:
        for provider_field in spec.auth_fields:
            if provider_field.secret and provider_field.name not in entered:
                secrets.pop(provider_field.name, None)

    for provider_field in spec.required_fields():
        if provider_field.kind is FieldKind.CHOICE or not provider_field.secret:
            continue
        if secrets.get(provider_field.name):
            continue
        entered[provider_field.name] = await _prompt_field(wizard, provider_name, provider_field, secrets)
        secrets[provider_field.name] = entered[provider_field.name]

    missing_names = missing_required(provider_name, config, secrets)
    if missing_names:
        raise ProviderError(
            _("{provider} still needs {fields}").format(provider=spec.label, fields=", ".join(missing_names))
        )

    sources = _field_sources(spec, supplied, entered, config, secrets)
    return CredentialSetup(dict(config), dict(secrets), sources)


async def choose_persistence(
    wizard: InitWizardApp,
    provider_name: str,
    setup: CredentialSetup,
) -> CredentialSetup:
    """Choose private storage, current environment, or this run only."""

    spec = get(provider_name).spec
    secret_names = [provider_field.name for provider_field in spec.auth_fields if provider_field.secret]
    active_sources = [setup.sources[name] for name in secret_names if name in setup.secrets]
    if active_sources and all(source is CredentialSource.SAVED for source in active_sources):
        wizard.note(_("Credentials are already stored privately in ~/.pykantui/auth.json"))
        return replace(setup, persistence=CredentialPersistence.PRIVATE_STORE)
    if active_sources and all(source is CredentialSource.ARGUMENT for source in active_sources):
        wizard.note(_("Credentials supplied by command line will be stored privately after validation"))
        return replace(setup, persistence=CredentialPersistence.PRIVATE_STORE)

    environment_only = bool(active_sources) and all(
        source in (CredentialSource.ENVIRONMENT, CredentialSource.SAVED) for source in active_sources
    )
    choices = _persistence_choices(spec, environment_only=environment_only)
    selected = await wizard.choose(
        choices,
        title=_("Keep {provider} credentials?").format(provider=spec.label),
        filter_hint=_("choose where credentials are reused"),
    )
    persistence = CredentialPersistence(selected)
    if persistence is CredentialPersistence.ENVIRONMENT:
        wizard.note(_("Using existing environment variables; no credential was copied"))
    elif persistence is CredentialPersistence.SESSION:
        names = _environment_names(spec)
        wizard.note(
            _("Credentials are not saved. For future runs, set: {names}").format(names=", ".join(names))
        )
    return replace(setup, persistence=persistence)


def _missing_non_choice(
    spec: ProviderSpec,
    config: Mapping[str, object],
    secrets: Mapping[str, str],
) -> list[ProviderField]:
    return [
        provider_field
        for provider_field in spec.required_fields()
        if provider_field.kind is not FieldKind.CHOICE
        and not (config.get(provider_field.name) or secrets.get(provider_field.name))
    ]


def _field_sources(
    spec: ProviderSpec,
    supplied: Mapping[str, str],
    entered: Mapping[str, str],
    config: Mapping[str, object],
    secrets: Mapping[str, str],
) -> dict[str, CredentialSource]:
    saved = load_secrets(spec.name, config)
    sources: dict[str, CredentialSource] = {}
    for provider_field in spec.all_fields():
        value = config.get(provider_field.name) or secrets.get(provider_field.name)
        if not value:
            continue
        if entered.get(provider_field.name):
            sources[provider_field.name] = CredentialSource.ENTERED
        elif supplied.get(provider_field.name):
            sources[provider_field.name] = CredentialSource.ARGUMENT
        elif provider_field.from_env() and environment_value_trusts_scope(spec.name, config, provider_field):
            sources[provider_field.name] = CredentialSource.ENVIRONMENT
        elif saved.get(provider_field.name):
            sources[provider_field.name] = CredentialSource.SAVED
        else:
            sources[provider_field.name] = CredentialSource.DEFAULT
    return sources


def _source_description(spec: ProviderSpec, sources: Mapping[str, CredentialSource]) -> str:
    lines: list[str] = []
    for provider_field in spec.required_fields():
        source = sources.get(provider_field.name)
        if source is None or provider_field.kind is FieldKind.CHOICE:
            continue
        origin = _source_detail(provider_field, source)
        lines.append(f"{provider_field.label}: {origin}")
    return "; ".join(lines) or _source_summary(sources)


def _authentication_choices(spec: ProviderSpec) -> list[Choice]:
    choices: list[Choice] = []
    if spec.token_url:
        if spec.credential_setup is CredentialSetupKind.PERSONAL:
            label = _("Get personal credentials")
            detail = _("no app registration")
        elif spec.credential_setup is CredentialSetupKind.PROVIDER_APPLICATION:
            label = _("Set up {provider} API access").format(provider=spec.label)
            detail = _("Power-Up registration required")
        else:
            label = _("Open provider credential page")
            detail = _("open the provider credential page")
        choices.append(
            Choice(
                value="authenticate",
                label=label,
                detail=detail,
                marker="↗",
                tone="cyan",
                description=spec.token_url,
            )
        )
    choices.append(
        Choice(
            value="enter",
            label=_("Enter existing credentials"),
            detail=", ".join(_environment_names(spec)),
            marker="•",
            tone="green",
            description=_("Paste the credential into a masked field."),
        )
    )
    return choices


def _persistence_choices(spec: ProviderSpec, *, environment_only: bool) -> list[Choice]:
    private = Choice(
        value=CredentialPersistence.PRIVATE_STORE.value,
        label=_("Save privately"),
        detail="~/.pykantui/auth.json",
        marker="✓",
        tone="green",
        description=_("Owner-only, outside every workspace and local Git history."),
    )
    if environment_only:
        return [
            Choice(
                value=CredentialPersistence.ENVIRONMENT.value,
                label=_("Use environment"),
                detail=", ".join(_environment_names(spec)),
                marker="•",
                tone="cyan",
                description=_("Keep using the value already exported or loaded from .env; do not copy it."),
            ),
            private,
        ]
    return [
        private,
        Choice(
            value=CredentialPersistence.SESSION.value,
            label=_("Use once"),
            detail=_("not saved"),
            marker="•",
            tone="yellow",
            description=_("Available for this setup only. Configure the listed environment variables for reuse."),
        ),
    ]


async def _open_authentication_page(
    wizard: InitWizardApp,
    spec: ProviderSpec,
    open_url: Callable[[str], bool],
) -> None:
    if not spec.token_url:
        return
    wizard.loading(_("Opening {provider} credential page").format(provider=spec.label))
    opened = await asyncio.to_thread(open_url, spec.token_url)
    if opened:
        wizard.done(_("Credential page opened"))
    else:
        wizard.done(_("Open this address in a browser: {url}").format(url=spec.token_url))


async def _prompt_field(
    wizard: InitWizardApp,
    provider_name: str,
    provider_field: ProviderField,
    secrets: Mapping[str, str],
) -> str:
    return await wizard.prompt(
        provider_field.label,
        note=_field_note(provider_name, provider_field, secrets),
        placeholder=provider_field.placeholder,
        secret=provider_field.secret,
    )


def _field_note(provider_name: str, provider_field: ProviderField, secrets: Mapping[str, str]) -> str:
    lines = [provider_field.help] if provider_field.help else []
    if provider_field.env_vars:
        lines.append(_("Environment: {names}").format(names=", ".join(provider_field.env_vars)))
    if provider_name == "trello" and provider_field.name == "key":
        lines.append("https://trello.com/apps/admin")
    elif provider_name == "trello" and provider_field.name == "token" and secrets.get("key"):
        from pykantui.providers.trello import token_url_for  # noqa: PLC0415

        lines.append(token_url_for(secrets["key"]))
    return "\n".join(lines)


def _source_detail(provider_field: ProviderField, source: CredentialSource) -> str:
    if source is CredentialSource.ENVIRONMENT and provider_field.env_vars:
        return f"{source.value} · {provider_field.env_vars[0]}"
    if source is CredentialSource.SAVED:
        return f"{source.value} · ~/.pykantui/auth.json"
    return source.value


def _source_summary(sources: Mapping[str, CredentialSource]) -> str:
    unique = list(dict.fromkeys(source.value for source in sources.values()))
    return " + ".join(unique)


def _environment_names(spec: ProviderSpec) -> list[str]:
    return [
        provider_field.env_vars[0]
        for provider_field in spec.auth_fields
        if provider_field.secret and provider_field.env_vars
    ]
