"""Render provider Sync states and confirmation from a throwaway workspace."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import date
from pathlib import Path

from tests.integration.sync.test_push import DOING, PROJECT, TODO, RecordingProvider, issue
from textual.widgets import Input, TextArea

from pykantui.commands.new import write_draft
from pykantui.i18n import Locale, using_locale
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.edit import TaskEditScreen
from pykantui.pages.grouped_palette import GroupedCommandPalette
from pykantui.pages.menu import ContextMenuScreen
from pykantui.providers import builtin_providers
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker import get
from pykantui.tracker.models import IssueDraft, RemoteProject
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.workspace import layout
from pykantui.workspace.project import Project
from pykantui.workspace.state import SyncState
from pykantui.workspace.sync import sync

SIZE = (140, 40)


def edit(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def build(workspace: Path) -> ProviderBackend:
    provider = RecordingProvider(
        [
            issue("K-1", TODO, title="Synced card"),
            issue("K-2", TODO, title="Edit this Markdown"),
            issue("K-3", DOING, title="Conflicting card"),
            issue("K-4", DOING, title="Invalid Markdown blocked"),
        ]
    )
    # Exercise the real Jira capabilities and labels while keeping the
    # transport deterministic and offline for visual verification.
    provider.spec = get("jira").spec  # type: ignore[misc]
    sync(workspace, provider, PROJECT, push_edits=False, commit=False)

    k2 = next(workspace.rglob("K-2.md"))
    edit(k2, "title: Edit this Markdown", "title: Edited locally in Markdown")
    k3 = next(workspace.rglob("K-3.md"))
    edit(k3, "Body K-3", "My local conflict text")
    k4 = next(workspace.rglob("K-4.md"))
    edit(k4, "title: Invalid Markdown blocked", "title: Invalid Markdown blocked\nlabels: backend")

    state = SyncState.load(layout.state_file(workspace))
    state.mark_conflicts({"id-K-3"})
    state.save(layout.state_file(workspace))

    record = Project(
        provider=provider.spec.name,
        project_id=PROJECT.project_id,
        key=PROJECT.key,
        name=PROJECT.name,
    )
    capabilities = provider.spec.capabilities.model_copy(update={"create_issues": True})
    provider.spec = provider.spec.model_copy(  # type: ignore[misc]
        update={"capabilities": capabilities}
    )
    write_draft(workspace, record, TODO, IssueDraft(title="Created as Markdown", body="A local draft."))

    # The preview sees a real same-field conflict, not only the status saved
    # above from an earlier sync.
    provider._issues = [
        item.model_copy(update={"body": "Provider changed this too"}) if item.key == "K-3" else item
        for item in provider._issues
    ]
    return ProviderBackend(workspace, provider, PROJECT)


async def render(into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        backend = build(Path(directory))

        statuses = KanbanApp(backend)
        async with statuses.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()
            statuses.save_screenshot(str(into / "sync-statuses.svg"))

        confirmation = KanbanApp(backend)
        async with confirmation.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            for _ in range(10):
                await pilot.pause()
                if confirmation.screen.id != "_default":
                    break
            confirmation.save_screenshot(str(into / "sync-confirmation.svg"))

    # A second isolated workspace shows the complete TUI-edit journey. Saving
    # changes Markdown and the status marker, but the recording provider must
    # remain untouched until the confirmation screen's send button is chosen.
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        provider = RecordingProvider(
            [
                issue(
                    "K-1",
                    TODO,
                    title="Review onboarding copy",
                    body="Replace the temporary onboarding text.",
                )
            ]
        )
        sync(workspace, provider, PROJECT, push_edits=False, commit=False)
        backend = ProviderBackend(workspace, provider, PROJECT)
        app = KanbanApp(backend)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            card = next(item for item in app.query(TaskCard) if item.task_.metadata.get("key") == "K-1")
            card.focus()
            await pilot.press("e")
            await pilot.pause()

            if not isinstance(app.screen, TaskDetailScreen):
                raise RuntimeError("the edit screen did not open")
            app.screen.query_one("#detail-summary", Input).value = "Publish polished onboarding copy"
            app.screen.query_one("#detail-notes", TextArea).load_text(
                "Use the approved welcome message and verify every link."
            )
            app.save_screenshot(str(into / "tui-editing.svg"))

            await pilot.press("ctrl+s")
            for _ in range(6):
                await pilot.pause()
            if provider.updates or provider.moves:
                raise RuntimeError("saving the TUI edit contacted the provider")
            app.save_screenshot(str(into / "tui-edit-saved-local.svg"))

            await pilot.press("f5")
            for _ in range(10):
                await pilot.pause()
                if app.screen.id != "_default":
                    break
            if provider.updates or provider.moves:
                raise RuntimeError("opening Sync contacted the provider")
            app.save_screenshot(str(into / "tui-edit-sync-preview.svg"))

    # One complete UI matrix per built-in provider. These use the real ProviderSpec
    # declarations with a recording transport, so the screenshots cannot
    # touch a live account while still carrying the provider's name and field
    # capabilities through the same backend the real TUI uses.
    for name in sorted(builtin_providers()):
        spec = get(name).spec
        project = RemoteProject(
            project_id=f"{name}-demo",
            key=name.upper(),
            name=f"{spec.label} · Demo board",
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            provider = RecordingProvider(
                [
                    issue(
                        f"{name[:3].upper()}-12",
                        TODO,
                        title=f"Edit a {spec.label} card",
                        body=f"This description belongs to {spec.label}.",
                        assignee="Jose",
                        issue_type="Task",
                        labels=("demo", "tui"),
                        priority="High",
                        due_date=date(2026, 8, 21),
                    )
                ]
            )
            provider.spec = spec  # type: ignore[misc]
            sync(workspace, provider, project, push_edits=False, commit=False)
            app = KanbanApp(ProviderBackend(workspace, provider, project))

            async with app.run_test(size=SIZE) as pilot:
                await pilot.pause()
                next(iter(app.query(TaskCard))).focus()
                await pilot.press("e")
                await pilot.pause()
                if not isinstance(app.screen, TaskDetailScreen):
                    raise RuntimeError(f"the {spec.label} edit screen did not open")
                app.save_screenshot(str(into / f"edit-{name}.svg"))

                await pilot.press("escape")
                await pilot.pause()
                if spec.capabilities.create_issues:
                    await pilot.press("n")
                    await pilot.pause()
                    if not isinstance(app.screen, TaskEditScreen):
                        raise RuntimeError(f"the {spec.label} create screen did not open")
                    app.save_screenshot(str(into / f"create-{name}.svg"))
                    await pilot.press("escape")
                    await pilot.pause()

                # Toolbar View pop-down, then the fully expanded provider
                # filter panel. Both are mounted Textual widgets, not mock art.
                await pilot.press("f2")
                await pilot.pause()
                await pilot.click("#bar-menu-view")
                await pilot.pause()
                if not isinstance(app.screen, ContextMenuScreen):
                    raise RuntimeError(f"the {spec.label} View menu did not open")
                app.save_screenshot(str(into / f"menu-view-{name}.svg"))
                await pilot.press("escape")
                await pilot.press("f2")
                await pilot.pause()
                app.save_screenshot(str(into / f"filters-{name}.svg"))

                await pilot.press("ctrl+p")
                await pilot.pause()
                if not isinstance(app.screen, GroupedCommandPalette):
                    raise RuntimeError(f"the {spec.label} application menu did not open")
                app.save_screenshot(str(into / f"menu-application-{name}.svg"))
                await pilot.press("escape")
                await pilot.pause()

                # Make a same-field conflict entirely offline and capture the
                # exact per-field Sync decision controls for this provider.
                card_path = next(workspace.rglob(f"{name[:3].upper()}-12.md"))
                edit(card_path, f"title: Edit a {spec.label} card", f"title: Local {spec.label} title")
                provider._issues = [
                    item.model_copy(update={"title": f"Provider {spec.label} title"})
                    for item in provider._issues
                ]
                await pilot.press("f5")
                for _ in range(10):
                    await pilot.pause()
                    if app.screen.id != "_default":
                        break
                app.save_screenshot(str(into / f"sync-{name}.svg"))

    # Theme, edge, and wide-script samples use the same deterministic board.
    # They make terminal palette/font regressions visible without claiming a
    # generated mock-up represents the running application.
    for theme in ("cyberpunk", "pykantui-dark", "vercel", "textual-dark"):
        with tempfile.TemporaryDirectory() as directory:
            app = KanbanApp(build(Path(directory)))
            async with app.run_test(size=SIZE) as pilot:
                app.theme = theme
                await pilot.pause()
                await pilot.press("f2", "f2")
                await pilot.pause()
                app.save_screenshot(str(into / f"theme-{theme}.svg"))
                app.add_class("edges-square")
                await pilot.pause()
                app.save_screenshot(str(into / f"theme-{theme}-square.svg"))

    for locale in (Locale.GERMAN, Locale.SIMPLIFIED_CHINESE, Locale.ARABIC, Locale.JAPANESE):
        with tempfile.TemporaryDirectory() as directory, using_locale(locale):
            app = KanbanApp(build(Path(directory)))
            async with app.run_test(size=SIZE) as pilot:
                await pilot.pause()
                await pilot.press("f5")
                for _ in range(10):
                    await pilot.pause()
                    if app.screen.id != "_default":
                        break
                app.save_screenshot(str(into / f"locale-{locale.value}.svg"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(render(arguments.into))
