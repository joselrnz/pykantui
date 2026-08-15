"""Registered-workspace discovery and safe process replacement."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pykantui.commands import projects as projects_command
from pykantui.config.paths import projects_path
from pykantui.tracker.errors import ProviderError
from pykantui.workspace.project import Project
from pykantui.workspace.registry import ProjectLink, ProjectRegistry


class ProjectsCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def workspace(
        self,
        folder: str,
        *,
        provider: str = "jira",
        project_id: str = "P1",
        key: str = "APP",
        name: str = "Application",
    ) -> ProjectLink:
        workspace = self.root / folder
        workspace.mkdir(parents=True)
        Project(
            provider=provider,
            project_id=project_id,
            key=key,
            name=name,
        ).save(workspace)
        return ProjectLink(
            provider=provider,
            project_id=project_id,
            key=key,
            name=name,
            workspace=str(workspace.resolve()),
        )

    @staticmethod
    def args(action: str | None = None, query: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(projects_action=action, query=query)

    def run_command(
        self,
        registry: ProjectRegistry,
        *,
        action: str | None = None,
        query: str | None = None,
    ) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch("pykantui.commands.projects.load_registry", return_value=registry),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            code = projects_command.run(self.args(action, query))
        return code, output.getvalue()

    def test_default_lists_five_registered_workspaces_without_opening_or_syncing(self) -> None:
        links = [
            self.workspace(
                f"workspace-{index}",
                project_id=f"P{index}",
                key=f"APP{index}",
                name=f"Application {index}",
            )
            for index in range(1, 6)
        ]

        with patch("pykantui.commands.projects.replace_with_workspace_board") as opened:
            code, output = self.run_command(ProjectRegistry(projects=links))

        self.assertEqual(0, code)
        self.assertEqual(5, output.count("workspace-"))
        self.assertIn("jira/APP1", output)
        self.assertIn("jira/APP5", output)
        opened.assert_not_called()

    def test_exact_provider_and_key_opens_the_unique_matching_workspace(self) -> None:
        wanted = self.workspace("wanted", project_id="P1", key="APP")
        other = self.workspace("other", project_id="P2", key="OPS")

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[wanted, other])),
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
        ):
            code = projects_command.run(self.args("open", "jira/APP"))

        self.assertEqual(0, code)
        opened.assert_called_once_with(wanted.workspace_path)

    def test_duplicate_names_never_select_the_first_workspace_silently(self) -> None:
        first = self.workspace("one", project_id="P1", key="ONE", name="Shared")
        second = self.workspace("two", project_id="P2", key="TWO", name="Shared")

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[first, second])),
            patch("pykantui.commands.projects.chooser.can_run", return_value=False),
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            code = projects_command.run(self.args("open", "Shared"))

        self.assertEqual(2, code)
        self.assertIn("matches 2", error.getvalue())
        self.assertIn("workspace path", error.getvalue())
        opened.assert_not_called()

    def test_interactive_ambiguous_name_uses_the_searchable_chooser(self) -> None:
        first = self.workspace("one", project_id="P1", key="ONE", name="Shared")
        second = self.workspace("two", project_id="P2", key="TWO", name="Shared")

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[first, second])),
            patch("pykantui.commands.projects.chooser.can_run", return_value=True),
            patch("pykantui.commands.projects.chooser.choose", return_value=second.workspace) as choose,
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
        ):
            code = projects_command.run(self.args("open", "Shared"))

        self.assertEqual(0, code)
        self.assertEqual(2, len(choose.call_args.args[0]))
        opened.assert_called_once_with(second.workspace_path)

    def test_missing_workspace_fails_without_falling_through_to_another_match(self) -> None:
        missing = ProjectLink(
            provider="jira",
            project_id="P1",
            key="APP",
            name="Application",
            workspace=str((self.root / "missing").resolve()),
        )

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[missing])),
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            code = projects_command.run(self.args("open", "APP"))

        self.assertEqual(2, code)
        self.assertIn("does not exist", error.getvalue())
        opened.assert_not_called()

    def test_workspace_metadata_must_match_the_registered_provider_and_project(self) -> None:
        link = self.workspace("mismatch", provider="jira", project_id="P1", key="APP")
        Project(provider="jira", project_id="P2", key="OTHER", name="Other").save(link.workspace_path)

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[link])),
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            code = projects_command.run(self.args("open", "APP"))

        self.assertEqual(2, code)
        self.assertIn("does not match", error.getvalue())
        opened.assert_not_called()

    def test_invalid_project_metadata_fails_clearly(self) -> None:
        link = self.workspace("invalid", provider="jira", project_id="P1", key="APP")
        project_file = link.workspace_path / ".pykantui" / "project.json"
        project_file.write_text('{"schema": 1, "provider": 42}', encoding="utf-8")

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[link])),
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            code = projects_command.run(self.args("open", "APP"))

        self.assertEqual(2, code)
        self.assertIn("invalid project metadata", error.getvalue())
        opened.assert_not_called()

    def test_corrupt_registry_is_reported_without_opening_anything(self) -> None:
        home = self.root / "home"
        with patch.dict(os.environ, {"PYKANTUI_HOME": str(home)}, clear=False):
            projects_path().parent.mkdir(parents=True)
            projects_path().write_text("{broken", encoding="utf-8")
            with (
                patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
                contextlib.redirect_stderr(io.StringIO()) as error,
            ):
                code = projects_command.run(self.args("open", "APP"))

        self.assertEqual(2, code)
        self.assertIn("could not read project registry", error.getvalue())
        opened.assert_not_called()

    def test_one_workspace_cannot_be_registered_to_two_projects(self) -> None:
        first = self.workspace("shared", project_id="P1", key="ONE")
        second = first.model_copy(update={"project_id": "P2", "key": "TWO"})

        with (
            patch(
                "pykantui.commands.projects.load_registry",
                return_value=ProjectRegistry(projects=[first, second]),
            ),
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            code = projects_command.run(self.args())

        self.assertEqual(2, code)
        self.assertIn("workspace more than once", error.getvalue())
        opened.assert_not_called()

    def test_relative_registry_path_is_not_resolved_from_the_callers_directory(self) -> None:
        link = ProjectLink(
            provider="jira",
            project_id="P1",
            key="APP",
            name="Application",
            workspace="relative/workspace",
        )

        with self.assertRaisesRegex(ProviderError, "not absolute"):
            projects_command.validate_registered_workspace(link)

    def test_exact_unicode_workspace_path_with_spaces_opens_without_shell_parsing(self) -> None:
        link = self.workspace(
            "Área de trabajo α with spaces",
            project_id="UNICODE-1",
            key="国際",
            name="Planificación",
        )

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[link])),
            patch("pykantui.commands.projects.replace_with_workspace_board") as opened,
        ):
            code = projects_command.run(self.args("open", str(link.workspace_path)))

        self.assertEqual(0, code)
        opened.assert_called_once_with(link.workspace_path)

    def test_open_is_registry_only_and_never_constructs_a_provider_or_syncs(self) -> None:
        link = self.workspace("safe")

        with (
            patch("pykantui.commands.projects.load_registry", return_value=ProjectRegistry(projects=[link])),
            patch("pykantui.commands.projects.replace_with_workspace_board"),
            patch("pykantui.workspace.project.Project.open") as provider_open,
            patch("pykantui.workspace.sync.sync") as sync,
        ):
            code = projects_command.run(self.args("open", str(link.workspace_path)))

        self.assertEqual(0, code)
        provider_open.assert_not_called()
        sync.assert_not_called()

    def test_cli_parser_exposes_projects_list_and_open(self) -> None:
        from pykantui.cli.main import build_parser

        listed = build_parser().parse_args(["projects"])
        opened = build_parser().parse_args(["projects", "open", "jira/APP"])

        self.assertEqual("projects", listed.command)
        self.assertIsNone(listed.projects_action)
        self.assertEqual("open", opened.projects_action)
        self.assertEqual("jira/APP", opened.query)

    def test_launcher_replaces_the_process_without_a_shell_or_nested_board(self) -> None:
        from pykantui.commands.launch import replace_with_workspace_board

        workspace = self.root / "Workspace with spaces 国際"
        workspace.mkdir()
        with (
            patch("pykantui.commands.launch.os.chdir") as change_directory,
            patch("pykantui.commands.launch._reattach_controlling_terminal") as reattach,
            patch("pykantui.commands.launch.os.execv", side_effect=SystemExit) as replace,
            self.assertRaises(SystemExit),
        ):
            replace_with_workspace_board(workspace)

        change_directory.assert_called_once_with(workspace)
        reattach.assert_called_once_with()
        executable, arguments = replace.call_args.args
        self.assertEqual(executable, arguments[0])
        self.assertEqual([executable, "-m", "pykantui"], arguments)


if __name__ == "__main__":
    unittest.main()
