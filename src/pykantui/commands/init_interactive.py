"""The complete interactive ``kbn init`` journey, inside one Textual app."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import cast

from pykantui.commands import init as init_command
from pykantui.commands.onboarding import (
    ProjectMatch,
    choice_field,
    choose_persistence,
    collect_credentials,
    connect_and_discover,
    match_configured_project,
    normalize_projects,
    project_blurb,
    project_config,
    project_noun,
    project_scope_label,
)
from pykantui.i18n import translate as _
from pykantui.pages.chooser import Choice
from pykantui.pages.init_wizard import INTRO_DURATION_SECONDS, InitWizardApp, WizardBack, WizardCancelled
from pykantui.tracker import AuthError, ProviderError, get, specs
from pykantui.tracker.base import Provider
from pykantui.tracker.models import RemoteProject
from pykantui.workspace import sync as sync_module
from pykantui.workspace.layout import ColumnStyle


class OnboardingStep(StrEnum):
    """Reversible stages before the workspace is written to disk."""

    PROVIDER = "provider"
    CREDENTIALS = "credentials"
    CONNECTION = "connection"
    PERSISTENCE = "persistence"
    PROJECT = "project"
    WORKSPACE = "workspace"


def run_interactive(args: argparse.Namespace) -> int:
    """Run onboarding full-screen, then transfer directly into the board."""
    interactive_output = sys.stdout.isatty()
    duration = INTRO_DURATION_SECONDS if interactive_output else 0.0
    app = InitWizardApp(
        lambda wizard: _journey(args, wizard),
        intro_duration=duration,
        acknowledge_completion=interactive_output,
    )
    workspace = app.run(inline=False)
    if workspace is None:
        return 130
    if getattr(args, "open_board", True):
        init_command._open_workspace(workspace)
    return 0


async def _journey(args: argparse.Namespace, wizard: InitWizardApp) -> Path:
    supplied = {name[2:]: value for name, value in vars(args).items() if name.startswith("f_") and value}
    provider_name = args.provider.lower() if args.provider else ""
    provider_label = provider_name
    if provider_name:
        _validate_provider_name(provider_name)
    step = OnboardingStep.CREDENTIALS if provider_name else OnboardingStep.PROVIDER
    setup = None
    provider: Provider | None = None
    projects: list[RemoteProject] = []
    project: RemoteProject | None = None
    workspace: Path | None = None
    force_entry = False

    try:
        while True:
            if step is OnboardingStep.PROVIDER:
                provider_name = await _choose_provider(wizard)
                _validate_provider_name(provider_name)
                setup = None
                projects = []
                project = None
                step = OnboardingStep.CREDENTIALS
                continue

            if step is OnboardingStep.CREDENTIALS:
                if provider is not None:
                    provider.close()
                    provider = None
                try:
                    setup = await collect_credentials(
                        wizard,
                        provider_name,
                        supplied,
                        force_entry=force_entry,
                    )
                except WizardBack:
                    if args.provider:
                        raise WizardCancelled from None
                    force_entry = False
                    step = OnboardingStep.PROVIDER
                    continue
                force_entry = False
                step = OnboardingStep.CONNECTION
                continue

            if step is OnboardingStep.CONNECTION:
                if setup is None:
                    raise RuntimeError("credential setup is unavailable")
                config = dict(setup.config)
                secrets = setup.secrets
                provider = get(provider_name)(config, secrets)
                provider_label = provider.spec.label
                try:
                    projects, _user = await connect_and_discover(wizard, provider)
                except AuthError as error:
                    try:
                        retry = await _retry_authentication(wizard, provider.spec.label, error)
                    except WizardBack:
                        retry = True
                    if not retry:
                        raise ProviderError(_("Authentication cancelled")) from error
                    force_entry = True
                    step = OnboardingStep.CREDENTIALS
                    continue
                step = OnboardingStep.PERSISTENCE
                continue

            if step is OnboardingStep.PERSISTENCE:
                if setup is None:
                    raise RuntimeError("credential setup is unavailable")
                try:
                    setup = await choose_persistence(wizard, provider_name, setup)
                except WizardBack:
                    force_entry = True
                    step = OnboardingStep.CREDENTIALS
                    continue
                step = OnboardingStep.PROJECT
                continue

            if step is OnboardingStep.PROJECT:
                if provider is None or setup is None:
                    raise RuntimeError("provider connection is unavailable")
                try:
                    project = await _choose_project(wizard, provider, projects, dict(setup.config))
                except WizardBack:
                    step = OnboardingStep.PERSISTENCE
                    continue
                setup = replace(
                    setup,
                    config=project_config(provider.spec, dict(setup.config), project),
                )
                wizard.done(_("Validated project: {project}").format(project=project.label()))
                step = OnboardingStep.WORKSPACE
                continue

            if project is None or provider is None or setup is None:
                raise RuntimeError("project selection is unavailable")
            try:
                workspace = await _choose_workspace(wizard, args, project)
            except WizardBack:
                step = OnboardingStep.PROJECT
                continue
            break
    finally:
        if provider is not None:
            provider.close()

    if workspace is None or project is None or setup is None:
        raise RuntimeError("setup did not produce a workspace")
    config = dict(setup.config)
    secrets = setup.secrets
    wizard.done(f"Location: {workspace}")

    wizard.loading("Creating the local workspace")
    await asyncio.to_thread(
        init_command._create,
        args,
        workspace,
        provider_name,
        project,
        config,
        secrets,
        verbose=False,
        save_credentials=setup.should_save,
    )
    wizard.done("Workspace ready")

    sync_summary = _("skipped")
    if args.do_sync:
        # Reopen a provider after the reversible selection phase has closed it.
        provider = get(provider_name)(config, secrets)
        try:
            wizard.loading(f"Pulling items from {provider.spec.label}")
            report = await asyncio.to_thread(
                sync_module.sync,
                workspace,
                provider,
                project,
                push_edits=False,
                commit=args.use_git,
                column_style=ColumnStyle(args.columns),
            )
        finally:
            provider.close()
        sync_summary = report.summary()
        wizard.done(f"Initial pull complete · {sync_summary}")
    else:
        wizard.done("Initial pull skipped")

    wizard.done(_("Setup complete"))
    await wizard.finish(
        provider=provider_label,
        project=project.label(),
        scope_label=project_scope_label(provider.spec),
        workspace=workspace,
        sync_summary=sync_summary,
        open_board=getattr(args, "open_board", True),
    )
    return workspace


def _validate_provider_name(provider_name: str) -> None:
    """Reject an unknown provider before collecting any credentials."""
    available = [spec.name for spec in specs()]
    if provider_name not in available:
        raise ProviderError(
            f"no tracker named {provider_name!r}",
            hint=f"Available: {', '.join(available)}",
        )


async def _retry_authentication(
    wizard: InitWizardApp,
    provider_label: str,
    error: ProviderError,
) -> bool:
    """Keep a rejected credential inside setup and offer a clean retry."""

    selected = await wizard.choose(
        [
            Choice(
                value="retry",
                label=_("Enter credentials again"),
                detail=_("retry authentication"),
                marker="↻",
                tone="cyan",
                description=str(error),
            ),
            Choice(
                value="cancel",
                label=_("Cancel setup"),
                detail=_("create nothing"),
                marker="×",
                tone="yellow",
                description=_("No workspace or credential file has been created."),
            ),
        ],
        title=_("Could not connect to {provider}").format(provider=provider_label),
        filter_hint=_("retry or cancel"),
    )
    return selected == "retry"


async def _choose_provider(wizard: InitWizardApp) -> str:
    available = specs()
    return await wizard.choose(
        [
            Choice(
                value=spec.name,
                label=spec.label,
                detail=spec.name,
                note="verified" if spec.verified else "not tested",
                marker="●" if spec.verified else "○",
                tone="green" if spec.verified else "yellow",
                description=init_command._tracker_blurb(spec),
                keywords=(spec.name, *(field.name for field in spec.all_fields())),
            )
            for spec in available
        ],
        title="Which tracker?",
        filter_hint="type to filter — jira, linear, trello…",
        allow_back=False,
    )


async def _choose_project(
    wizard: InitWizardApp,
    provider: Provider,
    available: list[RemoteProject],
    config: dict[str, object],
) -> RemoteProject:
    field = choice_field(provider.spec)
    supplied = str(config.get(field.name, "")) if field else ""
    available = normalize_projects(available)
    singular = project_noun(provider.spec, count=1)
    plural = project_noun(provider.spec, count=2)
    if not available:
        wizard.done(f"No {provider.spec.label} {plural} found")
    while not available:
        await wizard.wait_for_projects(
            provider.spec.label,
            scope_singular=singular,
            scope_plural=plural,
        )
        wizard.loading(f"Checking {provider.spec.label} {plural} again")
        available = normalize_projects(await asyncio.to_thread(provider.list_projects))
        if available:
            wizard.done(f"{provider.spec.label} {plural} found")
        else:
            wizard.done(f"No {provider.spec.label} {plural} found")

    configured = match_configured_project(supplied, available)
    if supplied and configured.kind is ProjectMatch.NONE:
        wizard.note(
            _("Configured project {project} is not visible; choose an available project").format(project=supplied)
        )
    elif configured.kind is ProjectMatch.AMBIGUOUS:
        wizard.note(
            f"Configured value {supplied} matches more than one accessible "
            f"{project_noun(provider.spec, count=2)}"
        )
    if configured.project is not None:
        available = [configured.project, *(project for project in available if project != configured.project)]

    labels = [project.name or project.key or project.project_id for project in available]
    duplicate_labels = {label for label in labels if labels.count(label) > 1}
    only_one = len(available) == 1
    selected = await wizard.choose(
        [
            Choice(
                value=project.project_id,
                label=project.name or project.key or project.project_id,
                detail=(
                    project.project_id
                    if (project.name or project.key or project.project_id) in duplicate_labels
                    else project.key or project.project_id
                ),
                note=(
                    "only accessible"
                    if only_one
                    else "configured"
                    if project == configured.project
                    else ""
                ),
                marker="▣",
                tone="cyan",
                description=project_blurb(project),
                keywords=(
                    project.project_id,
                    project.key,
                    project.name,
                    *(str(value) for value in project.extra.values()),
                ),
            )
            for project in available
        ],
        title=(
            f"Confirm {provider.spec.label} {project_noun(provider.spec, count=1)}"
            if only_one
            else f"Which {provider.spec.label} {project_noun(provider.spec, count=1)}?"
        ),
        filter_hint=f"type to filter {project_noun(provider.spec, count=2)}",
        allow_back=True,
    )
    return next(project for project in available if project.project_id == selected)


async def _choose_workspace(
    wizard: InitWizardApp,
    args: argparse.Namespace,
    project: RemoteProject,
) -> Path:
    supplied_path = cast(Path | None, args.path)
    if supplied_path is not None:
        return supplied_path.expanduser().resolve()
    name = (project.key or project.project_id).lower()
    if getattr(args, "browse", True):
        parent = await wizard.choose_folder(Path.cwd(), title=f"Where should {name} live?")
        return (parent / name).resolve()
    typed = await wizard.prompt("Workspace path", placeholder=str(Path.cwd() / name))
    return Path(typed).expanduser().resolve()
