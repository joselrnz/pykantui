"""Registered-workspace switching for the running board."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, cast

from textual import work

from pykantui.commands.projects import (
    project_choices,
    validate_registered_workspace,
    validate_registry_workspaces,
)
from pykantui.i18n import ntranslate as ngettext
from pykantui.i18n import translate as _
from pykantui.models import Task
from pykantui.pages.chooser import Chooser
from pykantui.pages.projects import ConfirmProjectSwitchScreen
from pykantui.tracker.errors import ProviderError
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.registry import ProjectLink, load_registry
from pykantui.workspace.status import SyncStatus

if TYPE_CHECKING:
    from pykantui.tui.app import KanbanApp


class ProjectController:
    """Leave this app with a validated workspace for the CLI to reopen."""

    @work(group="projects", exclusive=True)
    async def action_projects(self) -> None:
        app = cast("KanbanApp", self)
        work_items = app.query_one(WorkItemsView)
        if work_items.editor_active:
            app.notify(
                _("Save or cancel the open card edit before switching projects."),
                severity="warning",
                timeout=4,
            )
            return

        try:
            projects = load_registry().projects
            validate_registry_workspaces(projects)
        except ProviderError as error:
            app.notify(str(error), severity="error", timeout=6)
            return
        if not projects:
            app.notify(
                _("No registered project workspaces. Run `kbn init` to create one."),
                severity="warning",
                timeout=5,
            )
            return

        selected = await app.push_screen_wait(
            Chooser(
                project_choices(projects),
                title=_("Which project workspace?"),
                filter_hint=_("type to filter by provider, project, or path"),
            )
        )
        if not isinstance(selected, str):
            return
        link = next((item for item in projects if item.workspace == selected), None)
        if link is None:
            app.notify(_("That registry entry changed; open Projects again."), severity="warning", timeout=4)
            return
        await self._switch_to_project(link)

    async def _switch_to_project(self, link: ProjectLink) -> None:
        app = cast("KanbanApp", self)
        try:
            workspace = validate_registered_workspace(link)
        except ProviderError as error:
            app.notify(str(error), severity="error", timeout=6)
            return

        current = getattr(app.backend, "workspace", None)
        if isinstance(current, Path) and current.expanduser().resolve(strict=False) == workspace:
            app.notify(_("That project is already open."), timeout=3)
            return

        warning = switch_warning(app.backend.get_tasks()) if app.backend.supports_sync else ""
        if warning:
            approved = await app.push_screen_wait(
                ConfirmProjectSwitchScreen(
                    warning,
                    _("Opening {project}\n{workspace}").format(
                        project=f"{link.provider}/{link.key or link.project_id}",
                        workspace=workspace,
                    ),
                )
            )
            if not approved:
                return

        # KanbanApp historically returns ``None``. Textual's generic return
        # type cannot express this one navigation result without changing the
        # Pilot type of every board test; ``cast`` is runtime-neutral and the
        # CLI validates the result before reopening it.
        app.exit(cast(None, workspace))


def switch_warning(tasks: list[Task]) -> str:
    """Describe local work that remains safe on disk after switching away."""

    values = {status.value: status for status in SyncStatus}
    counts = Counter(
        values[raw]
        for task in tasks
        if (raw := str(task.metadata.get("sync_status", "") or "")) in values
        and values[raw] is not SyncStatus.SYNCED
    )
    parts: list[str] = []
    labels = (
        (SyncStatus.EDITED, "unsent edit", "unsent edits"),
        (SyncStatus.NEW, "unsynced card", "unsynced cards"),
        (SyncStatus.CONFLICT, "conflict", "conflicts"),
        (SyncStatus.INVALID, "invalid Markdown file", "invalid Markdown files"),
    )
    for status, singular, plural in labels:
        count = counts[status]
        if count:
            parts.append(f"{count} {ngettext(singular, plural, count)}")
    if not parts:
        return ""
    return _(
        "This workspace has {changes}. They remain on disk and are not synced automatically."
    ).format(changes=", ".join(parts))


__all__ = ["ProjectController", "switch_warning"]
