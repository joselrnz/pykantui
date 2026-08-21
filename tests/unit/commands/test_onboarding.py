"""Credential and connection behavior for the interactive init wizard."""

from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from pykantui.commands import init as init_command
from pykantui.commands.onboarding.connection import connect_and_discover
from pykantui.commands.onboarding.credentials import (
    _authentication_choices,
    choose_persistence,
    collect_credentials,
)
from pykantui.commands.onboarding.models import (
    CredentialPersistence,
    CredentialSetup,
    CredentialSource,
)
from pykantui.config.paths import auth_path
from pykantui.pages.init_wizard import InitWizardApp, WizardBack
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import RemoteProject, RemoteUser
from pykantui.tracker.registry import get
from pykantui.tracker.spec import CredentialSetupKind
from pykantui.workspace.credentials import save_secrets
from pykantui.workspace.layout import ColumnStyle, project_file


class OnboardingCredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._environment = patch.dict(
            os.environ,
            {
                "PYKANTUI_HOME": self._home.name,
                "GITHUB_TOKEN": "",
                "GH_TOKEN": "",
                "GITHUB_API_URL": "",
            },
            clear=False,
        )
        self._environment.start()

    def tearDown(self) -> None:
        self._environment.stop()
        self._home.cleanup()

    async def test_environment_credentials_are_detected_without_reentering_them(self) -> None:
        wizard = AsyncMock(spec=InitWizardApp)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "environment-secret"}):
            setup = await collect_credentials(wizard, "github", {"repo": "owner/repo"})

        self.assertEqual("environment-secret", setup.secrets["token"])
        self.assertEqual(CredentialSource.ENVIRONMENT, setup.sources["token"])
        wizard.prompt.assert_not_awaited()
        wizard.choose.assert_not_awaited()
        rendered_note = wizard.note.call_args.args[0]
        self.assertIn("GITHUB_TOKEN", rendered_note)
        self.assertNotIn("environment-secret", rendered_note)

    async def test_saved_credentials_are_detected_for_the_matching_origin(self) -> None:
        config = {"base_url": "https://api.github.com", "repo": "owner/repo"}
        save_secrets("github", {"token": "saved-secret"}, config=config)
        wizard = AsyncMock(spec=InitWizardApp)

        setup = await collect_credentials(wizard, "github", {"repo": "owner/repo"})

        self.assertEqual("saved-secret", setup.secrets["token"])
        self.assertEqual(CredentialSource.SAVED, setup.sources["token"])
        wizard.prompt.assert_not_awaited()
        self.assertNotIn("saved-secret", repr(setup))

    async def test_authenticate_opens_the_provider_page_then_masks_the_key_prompt(self) -> None:
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "authenticate"
        wizard.prompt.return_value = "entered-secret"
        open_url = Mock(return_value=True)

        setup = await collect_credentials(wizard, "github", {"repo": "owner/repo"}, open_url=open_url)

        open_url.assert_called_once_with("https://github.com/settings/tokens")
        wizard.prompt.assert_awaited_once()
        self.assertTrue(wizard.prompt.await_args.kwargs["secret"])
        self.assertEqual(CredentialSource.ENTERED, setup.sources["token"])
        self.assertEqual("entered-secret", setup.secrets["token"])
        self.assertNotIn("entered-secret", repr(setup))

    async def test_retry_does_not_reuse_a_rejected_environment_key(self) -> None:
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "enter"
        wizard.prompt.return_value = "replacement-secret"

        with patch.dict(os.environ, {"GITHUB_TOKEN": "rejected-secret"}):
            setup = await collect_credentials(
                wizard,
                "github",
                {"repo": "owner/repo"},
                force_entry=True,
            )

        self.assertEqual("replacement-secret", setup.secrets["token"])
        self.assertEqual(CredentialSource.ENTERED, setup.sources["token"])
        self.assertNotIn("rejected-secret", repr(wizard.choose.call_args))

    async def test_entered_key_can_be_saved_privately_or_kept_for_this_run(self) -> None:
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "enter"
        wizard.prompt.return_value = "entered-secret"
        setup = await collect_credentials(wizard, "github", {"repo": "owner/repo"})

        wizard.choose.reset_mock()
        wizard.choose.return_value = CredentialPersistence.PRIVATE_STORE.value
        saved = await choose_persistence(wizard, "github", setup)
        self.assertEqual(CredentialPersistence.PRIVATE_STORE, saved.persistence)

        wizard.choose.return_value = CredentialPersistence.SESSION.value
        session = await choose_persistence(wizard, "github", setup)
        self.assertEqual(CredentialPersistence.SESSION, session.persistence)
        self.assertIn("GITHUB_TOKEN", wizard.note.call_args.args[0])
        self.assertNotIn("entered-secret", wizard.note.call_args.args[0])

    async def test_environment_value_is_reused_without_copying_it_to_auth_store(self) -> None:
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = CredentialPersistence.ENVIRONMENT.value

        with patch.dict(os.environ, {"GITHUB_TOKEN": "environment-secret"}):
            setup = await collect_credentials(wizard, "github", {"repo": "owner/repo"})
            decided = await choose_persistence(wizard, "github", setup)

        self.assertEqual(CredentialPersistence.ENVIRONMENT, decided.persistence)
        self.assertFalse(decided.should_save)

    def test_personal_token_page_does_not_imply_oauth_app_registration(self) -> None:
        spec = get("asana").spec

        choices = _authentication_choices(spec)

        self.assertIs(CredentialSetupKind.PERSONAL, spec.credential_setup)
        self.assertEqual("Get personal credentials", choices[0].label)
        self.assertEqual("Personal access token", spec.auth_fields[0].label)
        self.assertEqual("no app registration", choices[0].detail)
        self.assertNotIn("Authenticate with", choices[0].label)
        self.assertEqual("Enter existing credentials", choices[1].label)

    def test_provider_registration_requirement_is_disclosed_before_opening_page(self) -> None:
        spec = get("trello").spec

        choices = _authentication_choices(spec)

        self.assertIs(CredentialSetupKind.PROVIDER_APPLICATION, spec.credential_setup)
        self.assertEqual("Set up Trello API access", choices[0].label)
        self.assertEqual("Power-Up registration required", choices[0].detail)
        self.assertEqual(("API key", "API token"), tuple(field.label for field in spec.auth_fields))


class OnboardingConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_projects_identity_and_selection_are_checked_in_visible_order(self) -> None:
        events: list[str] = []
        project = RemoteProject(project_id="P1", key="APP", name="Application")
        provider = Mock()
        provider.spec = get("github").spec

        def list_projects() -> list[RemoteProject]:
            events.append("projects")
            return [project]

        def verify() -> RemoteUser:
            events.append("identity")
            return RemoteUser(account_id="U1", display_name="Alex")

        provider.list_projects.side_effect = list_projects
        provider.verify.side_effect = verify
        wizard = AsyncMock(spec=InitWizardApp)

        projects, user = await connect_and_discover(wizard, cast(Provider, provider))

        self.assertEqual(["projects", "identity"], events)
        self.assertEqual([project], projects)
        self.assertEqual("Alex", user.label())
        self.assertIn("1 repository", wizard.done.call_args_list[0].args[0])
        self.assertIn("Alex", wizard.done.call_args_list[1].args[0])


class OnboardingNavigationTests(unittest.IsolatedAsyncioTestCase):
    def _args(self, workspace: Path) -> argparse.Namespace:
        return argparse.Namespace(
            provider=None,
            path=workspace,
            browse=True,
            do_sync=False,
            use_git=False,
            columns=ColumnStyle.SLUG.value,
            open_board=False,
        )

    async def test_back_from_project_returns_to_credential_persistence(self) -> None:
        from pykantui.commands.init_interactive import _journey

        project = RemoteProject(project_id="owner/repo", key="repo", name="Repository")
        user = RemoteUser(account_id="U1", display_name="Alex")
        setup = CredentialSetup(
            {"repo": "owner/repo"},
            {"token": "secret"},
            {"token": CredentialSource.ENTERED},
        )
        provider = Mock()
        provider.spec = get("github").spec
        provider_factory = Mock(return_value=provider)
        wizard = AsyncMock(spec=InitWizardApp)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "repo"
            with (
                patch("pykantui.commands.init_interactive._choose_provider", new=AsyncMock(return_value="github")),
                patch("pykantui.commands.init_interactive.collect_credentials", new=AsyncMock(return_value=setup)),
                patch(
                    "pykantui.commands.init_interactive.connect_and_discover",
                    new=AsyncMock(return_value=([project], user)),
                ),
                patch(
                    "pykantui.commands.init_interactive.choose_persistence",
                    new=AsyncMock(return_value=setup),
                ) as persistence,
                patch(
                    "pykantui.commands.init_interactive._choose_project",
                    new=AsyncMock(side_effect=[WizardBack(), project]),
                ),
                patch("pykantui.commands.init_interactive.get", return_value=provider_factory),
                patch.object(init_command, "_create"),
            ):
                result = await _journey(self._args(workspace), wizard)

        self.assertEqual(workspace, result)
        self.assertEqual(2, persistence.await_count)
        provider.close.assert_called_once_with()

    async def test_back_from_workspace_returns_to_project_selection(self) -> None:
        from pykantui.commands.init_interactive import _journey

        project = RemoteProject(project_id="owner/repo", key="repo", name="Repository")
        user = RemoteUser(account_id="U1", display_name="Alex")
        setup = CredentialSetup(
            {"repo": "owner/repo"},
            {"token": "secret"},
            {"token": CredentialSource.ENTERED},
        )
        provider = Mock()
        provider.spec = get("github").spec
        provider_factory = Mock(return_value=provider)
        wizard = AsyncMock(spec=InitWizardApp)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "repo"
            choose_project = AsyncMock(side_effect=[project, project])
            choose_workspace = AsyncMock(side_effect=[WizardBack(), workspace])
            with (
                patch("pykantui.commands.init_interactive._choose_provider", new=AsyncMock(return_value="github")),
                patch("pykantui.commands.init_interactive.collect_credentials", new=AsyncMock(return_value=setup)),
                patch(
                    "pykantui.commands.init_interactive.connect_and_discover",
                    new=AsyncMock(return_value=([project], user)),
                ),
                patch("pykantui.commands.init_interactive.choose_persistence", new=AsyncMock(return_value=setup)),
                patch("pykantui.commands.init_interactive._choose_project", new=choose_project),
                patch("pykantui.commands.init_interactive._choose_workspace", new=choose_workspace),
                patch("pykantui.commands.init_interactive.get", return_value=provider_factory),
                patch.object(init_command, "_create"),
            ):
                result = await _journey(self._args(workspace), wizard)

        self.assertEqual(workspace, result)
        self.assertEqual(2, choose_project.await_count)
        self.assertEqual(2, choose_workspace.await_count)
        provider.close.assert_called_once_with()

    async def test_completed_journey_marks_setup_complete_before_opening_board(self) -> None:
        from pykantui.commands.init_interactive import _journey

        project = RemoteProject(project_id="owner/repo", key="repo", name="Repository")
        user = RemoteUser(account_id="U1", display_name="Alex")
        setup = CredentialSetup(
            {"repo": "owner/repo"},
            {"token": "secret"},
            {"token": CredentialSource.ENTERED},
        )
        provider = Mock()
        provider.spec = get("github").spec
        provider_factory = Mock(return_value=provider)
        wizard = AsyncMock(spec=InitWizardApp)
        events: list[tuple[str, str]] = []
        wizard.done.side_effect = lambda message: events.append(("done", message))

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "repo"
            args = self._args(workspace)
            args.open_board = True
            with (
                patch("pykantui.commands.init_interactive._choose_provider", new=AsyncMock(return_value="github")),
                patch("pykantui.commands.init_interactive.collect_credentials", new=AsyncMock(return_value=setup)),
                patch(
                    "pykantui.commands.init_interactive.connect_and_discover",
                    new=AsyncMock(return_value=([project], user)),
                ),
                patch("pykantui.commands.init_interactive.choose_persistence", new=AsyncMock(return_value=setup)),
                patch("pykantui.commands.init_interactive._choose_project", new=AsyncMock(return_value=project)),
                patch("pykantui.commands.init_interactive.get", return_value=provider_factory),
                patch.object(init_command, "_create"),
            ):
                result = await _journey(args, wizard)

        self.assertEqual(workspace, result)
        self.assertEqual(("done", "Setup complete"), events[-1])
        wizard.finish.assert_awaited_once_with(
            provider="GitHub",
            project=project.label(),
            scope_label="Repository",
            workspace=workspace,
            sync_summary="skipped",
            open_board=True,
        )


class OnboardingWorkspaceTests(unittest.TestCase):
    @staticmethod
    def _versioned_args() -> argparse.Namespace:
        return argparse.Namespace(
            name="",
            columns=ColumnStyle.SLUG.value,
            use_git=True,
        )

    def test_requested_git_must_be_available_before_workspace_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            with (
                patch("pykantui.commands.init.git.available", return_value=False),
                self.assertRaisesRegex(ProviderError, "Git is unavailable"),
            ):
                init_command._create(
                    self._versioned_args(),
                    workspace,
                    "github",
                    RemoteProject(project_id="owner/repo", key="repo", name="Repository"),
                    {"base_url": "https://api.github.com", "repo": "owner/repo"},
                    {},
                    verbose=False,
                    save_credentials=False,
                )

            self.assertFalse(workspace.exists())

    def test_failed_requested_git_init_never_creates_project_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            with (
                patch("pykantui.commands.init.git.available", return_value=True),
                patch("pykantui.commands.init.git.init", return_value=False),
                self.assertRaisesRegex(ProviderError, "initialize local Git"),
            ):
                init_command._create(
                    self._versioned_args(),
                    workspace,
                    "github",
                    RemoteProject(project_id="owner/repo", key="repo", name="Repository"),
                    {"base_url": "https://api.github.com", "repo": "owner/repo"},
                    {},
                    verbose=False,
                    save_credentials=False,
                )

            self.assertFalse(project_file(workspace).exists())

    def test_session_only_credentials_never_create_auth_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            args = argparse.Namespace(
                name="",
                columns=ColumnStyle.SLUG.value,
                use_git=False,
            )
            with patch.dict(os.environ, {"PYKANTUI_HOME": str(root / "home")}, clear=False):
                init_command._create(
                    args,
                    workspace,
                    "github",
                    RemoteProject(project_id="owner/repo", key="repo", name="Repository"),
                    {"base_url": "https://api.github.com", "repo": "owner/repo"},
                    {"token": "session-secret"},
                    verbose=False,
                    save_credentials=False,
                )

                self.assertTrue(project_file(workspace).is_file())
                self.assertFalse(auth_path().exists())


if __name__ == "__main__":
    unittest.main()
