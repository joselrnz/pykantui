"""Read-only provider checks performed before a workspace is created."""

from __future__ import annotations

import asyncio

from pykantui.commands.onboarding.projects import project_noun
from pykantui.i18n import translate as _
from pykantui.pages.init_wizard import InitWizardApp
from pykantui.tracker.base import Provider
from pykantui.tracker.models import RemoteProject, RemoteUser


async def connect_and_discover(
    wizard: InitWizardApp,
    provider: Provider,
) -> tuple[list[RemoteProject], RemoteUser]:
    """List visible projects, then confirm the authenticated identity.

    Both operations are read-only and happen before any folder, credential
    file, or Git repository is created.  Keeping them here makes their order
    explicit and directly testable for every provider.
    """

    label = provider.spec.label
    wizard.loading(_("Listing {provider} projects").format(provider=label))
    projects = await asyncio.to_thread(provider.list_projects)
    noun = _(project_noun(provider.spec, count=len(projects)))
    wizard.done(_("{count} {noun} available").format(count=len(projects), noun=noun))

    wizard.loading(_("Confirming {provider} identity").format(provider=label))
    user = await asyncio.to_thread(provider.verify)
    wizard.done(_("Connected as {identity}").format(identity=user.label()))
    return projects, user
