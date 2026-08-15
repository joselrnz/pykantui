"""Switching registered workspaces without nesting boards or losing drafts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from pykantui.core.actions import Act, Action, ActionKind, Menu
from pykantui.models import Task
from pykantui.pages.chooser import Chooser
from pykantui.pages.grouped_palette import PaletteGroup
from pykantui.pages.projects import ConfirmProjectSwitchScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.project import Project
from pykantui.workspace.registry import ProjectLink, ProjectRegistry
from tests.integration.tui.test_menu_bar import SIZE, config_of, make_app, settle


class DirtyWorkspaceBackend(JsonBackend):
    supports_sync = True

    def __init__(self, status: str) -> None:
        super().__init__(config=config_of("To Do", "Done"))
        self.create_task(
            Task(
                task_id=1,
                title="Local work",
                column_id=1,
                metadata={"sync_status": status},
            )
        )


class ProjectSwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        workspace = Path(self._temporary.name) / "target with spaces"
        workspace.mkdir()
        Project(provider="jira", project_id="P1", key="APP", name="Application").save(workspace)
        self.link = ProjectLink(
            provider="jira",
            project_id="P1",
            key="APP",
            name="Application",
            workspace=str(workspace.resolve()),
        )
        self.registry = ProjectRegistry(projects=[self.link])

    def tearDown(self) -> None:
        self._temporary.cleanup()

    async def test_main_menu_and_palette_share_one_projects_action(self) -> None:
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            project_items = [
                item
                for item in app._menu_items(Menu.MAIN)
                if (action := Action.parse(item.key)) is not None
                and action.kind is ActionKind.ACT
                and action.enum(Act) is Act.PROJECTS
            ]

            self.assertEqual(1, len(project_items))
            self.assertEqual("Projects…", project_items[0].label)
            board = cast(PaletteGroup, app._palette_tree()[0])
            self.assertTrue(any(getattr(child, "label", "") == "Projects…" for child in board.children))

    async def test_clean_selection_exits_with_the_validated_workspace(self) -> None:
        app = make_app()
        with (
            patch("pykantui.tui.controllers.projects.load_registry", return_value=self.registry),
            patch("pykantui.workspace.project.Project.open") as provider_open,
            patch("pykantui.workspace.sync.sync") as sync,
        ):
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                switching = app.action_projects()
                await pilot.pause()
                self.assertIsInstance(app.screen, Chooser)
                await pilot.press("enter")
                await switching.wait()
                await pilot.pause()

        self.assertEqual(self.link.workspace_path, app.return_value)
        provider_open.assert_not_called()
        sync.assert_not_called()

    async def test_unsent_markdown_requires_explicit_switch_confirmation(self) -> None:
        app = make_app(DirtyWorkspaceBackend("edited"))
        with patch("pykantui.tui.controllers.projects.load_registry", return_value=self.registry):
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                switching = app.action_projects()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                self.assertIsInstance(app.screen, ConfirmProjectSwitchScreen)
                warning = app.screen.query_one("#project-switch-warning").render()
                self.assertIn("1 unsent edit", str(warning))

                await pilot.press("escape")
                await switching.wait()
                await pilot.pause()

                self.assertIsNone(app.return_value)

    async def test_active_inline_editor_blocks_switch_before_showing_a_picker(self) -> None:
        app = make_app()
        with patch("pykantui.tui.controllers.projects.load_registry", return_value=self.registry):
            async with app.run_test(size=SIZE) as pilot:
                await settle(pilot)
                work_items = app.query_one(WorkItemsView)
                work_items.editing = True

                switching = app.action_projects()
                await switching.wait()
                await pilot.pause()

                self.assertEqual("_default", app.screen.id)
                self.assertIsNone(app.return_value)


if __name__ == "__main__":
    unittest.main()
