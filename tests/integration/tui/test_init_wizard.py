"""The interactive init journey remains inside one Textual application."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from rich.cells import cell_len
from textual.geometry import Size
from textual.widgets import Button, Footer, Input, Static

from pykantui.commands.init_interactive import _choose_project, _retry_authentication
from pykantui.commands.onboarding.credentials import collect_credentials
from pykantui.pages.chooser import Choice, Chooser
from pykantui.pages.folder import FolderPicker
from pykantui.pages.init_wizard import (
    INTRO_DURATION_SECONDS,
    InitWizardApp,
    WizardBack,
    WizardComplete,
    WizardEmptyProjects,
    WizardMessage,
    WizardPrompt,
    _animate_logo_text,
    _intro_stage,
    _progress_rail,
    _styled_intro,
    _styled_stage,
    _sync_rail,
)
from pykantui.tracker import Provider, ProviderError
from pykantui.tracker.models import RemoteProject
from pykantui.tracker.spec import FieldKind, ProviderField, ProviderSpec


def _ink(value: str) -> int:
    """Count visible cells in a rendered intro frame."""
    return sum(not character.isspace() for character in value.rsplit("\n", maxsplit=1)[0])


class InitWizardTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_stays_visible_until_open_board_is_acknowledged(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            await wizard.finish(
                provider="Asana",
                project="Study schedule",
                workspace=Path("/work/study schedule"),
                sync_summary="wrote 5",
                open_board=True,
            )
            return Path("/work/study schedule")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()

            self.assertIsInstance(app.screen, WizardComplete)
            self.assertIsNone(app.return_value)
            rendered = str(app.screen.query_one("#wizard-complete-summary", Static).content)
            self.assertIn("Asana", rendered)
            self.assertIn("Study schedule", rendered)
            self.assertIn(str(Path("/work/study schedule")), rendered)
            self.assertIn("wrote 5", rendered)
            self.assertNotIn("token", rendered.casefold())
            self.assertEqual(
                "Open board",
                str(app.screen.query_one("#wizard-complete-open", Button).label),
            )

            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(Path("/work/study schedule"), app.return_value)

    async def test_completion_uses_finish_label_when_board_will_not_open(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            await wizard.finish(
                provider="GitHub",
                project="pykantui",
                workspace=Path("/work/pykantui"),
                sync_summary="skipped",
                open_board=False,
            )
            return Path("/work/pykantui")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertEqual(
                "Finish",
                str(app.screen.query_one("#wizard-complete-open", Button).label),
            )
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(Path("/work/pykantui"), app.return_value)

    async def test_completion_uses_the_provider_native_scope_label(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            await wizard.finish(
                provider="GitHub",
                project="acme/pykantui",
                scope_label="Repository",
                workspace=Path("/work/pykantui"),
                sync_summary="wrote 3",
                open_board=True,
            )
            return Path("/work/pykantui")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            rendered = str(app.screen.query_one("#wizard-complete-summary", Static).content)
            self.assertIn("Repository", rendered)
            self.assertNotIn("Project", rendered)
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(Path("/work/pykantui"), app.return_value)

    async def test_back_returns_to_the_previous_step_without_exiting_setup(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            while True:
                provider = await wizard.choose(
                    [Choice(value="jira", label="Jira")],
                    title="Which tracker?",
                    allow_back=False,
                )
                try:
                    project = await wizard.choose(
                        [Choice(value="APP", label="Application")],
                        title="Which Jira project?",
                    )
                except WizardBack:
                    continue
                return Path(f"/{provider}/{project}")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertEqual("Which tracker?", str(app.screen.query_one("#chooser-title").render()))
            self.assertEqual(0, len(app.screen.query("#chooser-back")))

            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual("Which Jira project?", str(app.screen.query_one("#chooser-title").render()))
            self.assertEqual("← Back", str(app.screen.query_one("#chooser-back", Button).label))

            await pilot.click("#chooser-back")
            await pilot.pause()
            self.assertEqual("Which tracker?", str(app.screen.query_one("#chooser-title").render()))
            self.assertIsNone(app.return_value)

            await pilot.press("enter", "enter")
            await pilot.pause()

        self.assertEqual(Path("/jira/APP"), app.return_value)

    async def test_prompt_back_is_separate_from_cancel(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            try:
                await wizard.prompt("API token")
            except WizardBack:
                return Path("/back")
            return Path("/continued")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertEqual("← Back", str(app.screen.query_one("#wizard-prompt-back", Button).label))
            self.assertEqual("Cancel", str(app.screen.query_one("#wizard-prompt-cancel", Button).label))
            await pilot.press("ctrl+b")
            await pilot.pause()

        self.assertEqual(Path("/back"), app.return_value)

    async def test_folder_picker_back_does_not_navigate_to_the_parent_folder(self) -> None:
        start = Path.cwd()

        async def journey(wizard: InitWizardApp) -> Path | None:
            try:
                await wizard.choose_folder(start, title="Where should it live?")
            except WizardBack:
                return Path("/back")
            return Path("/continued")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            picker = cast(FolderPicker, app.screen)
            self.assertEqual(start.resolve(), picker.chosen)
            self.assertEqual("← Back", str(picker.query_one("#folder-back", Button).label))
            await pilot.click("#folder-back")
            await pilot.pause()

        self.assertEqual(Path("/back"), app.return_value)

    async def test_cancel_still_exits_instead_of_going_back(self) -> None:
        continued = False

        async def journey(wizard: InitWizardApp) -> Path | None:
            nonlocal continued
            await wizard.choose(
                [Choice(value="APP", label="Application")],
                title="Which project?",
            )
            continued = True
            return Path("/continued")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            await pilot.click("#chooser-cancel")
            await pilot.pause()

        self.assertFalse(continued)
        self.assertIsNone(app.return_value)

    async def test_missing_credentials_open_a_boxed_authentication_choice(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            setup = await collect_credentials(
                wizard,
                "github",
                {"repo": "owner/repo"},
                open_url=lambda _url: False,
            )
            return Path("/authenticated") if setup.secrets.get("token") else None

        with tempfile.TemporaryDirectory() as home:
            environment = {
                "PYKANTUI_HOME": home,
                "GITHUB_TOKEN": "",
                "GH_TOKEN": "",
                "GITHUB_API_URL": "",
            }
            with patch.dict(os.environ, environment, clear=False):
                app = InitWizardApp(journey, intro_duration=0)
                async with app.run_test(size=(100, 38)) as pilot:
                    await pilot.pause()
                    self.assertIsInstance(app.screen, Chooser)
                    title = str(app.screen.query_one("#chooser-title").render())
                    self.assertEqual("Set up GitHub credentials", title)

                    await pilot.press("down", "enter")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, WizardPrompt)
                    field = app.screen.query_one("#wizard-input", Input)
                    self.assertTrue(field.password)
                    await pilot.press("t", "o", "k", "e", "n", "enter")
                    await pilot.pause()

        self.assertEqual(Path("/authenticated"), app.return_value)

    async def test_empty_account_stays_in_a_retryable_tui_dialog(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            await wizard.wait_for_projects("Jira")
            return Path("/retried")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, WizardEmptyProjects)
            title = str(app.screen.query_one("#wizard-empty-title", Static).content)
            body = str(app.screen.query_one("#wizard-empty-body", Static).content)
            retry = app.screen.query_one("#wizard-empty-retry", Button)

            self.assertEqual("No Jira projects found", title)
            self.assertIn("Create a project in Jira", body)
            self.assertIn("No local files", body)
            self.assertEqual("Check again", str(retry.label))

            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(Path("/retried"), app.return_value)

    async def test_empty_account_uses_the_provider_native_container_name(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            await wizard.wait_for_projects(
                "GitHub",
                scope_singular="repository",
                scope_plural="repositories",
            )
            return Path("/retried")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            title = str(app.screen.query_one("#wizard-empty-title", Static).content)
            body = str(app.screen.query_one("#wizard-empty-body", Static).content)

            self.assertEqual("No GitHub repositories found", title)
            self.assertIn("Create a repository in GitHub", body)
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(Path("/retried"), app.return_value)

    async def test_empty_account_can_cancel_without_creating_anything(self) -> None:
        journey_continued = False

        async def journey(wizard: InitWizardApp) -> Path | None:
            nonlocal journey_continued
            await wizard.wait_for_projects("Jira")
            journey_continued = True
            return Path("/unexpected")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, WizardEmptyProjects)
            await pilot.press("escape")
            await pilot.pause()

        self.assertFalse(journey_continued)
        self.assertIsNone(app.return_value)

    async def test_project_retry_requeries_the_provider_inside_the_wizard(self) -> None:
        project = RemoteProject(project_id="P1", key="APP", name="Application")
        provider = Mock()
        provider.spec = ProviderSpec(
            name="fake",
            label="FakeTracker",
            config_fields=(
                ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),
            ),
        )
        provider.list_projects.return_value = [project]
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "P1"

        selected = await _choose_project(
            wizard,
            cast(Provider, provider),
            [],
            {},
        )

        wizard.wait_for_projects.assert_awaited_once_with(
            "FakeTracker",
            scope_singular="project",
            scope_plural="projects",
        )
        provider.list_projects.assert_called_once_with()
        self.assertEqual(project, selected)

    async def test_only_visible_project_is_preselected_but_requires_visible_confirmation(self) -> None:
        project = RemoteProject(project_id="P1", key="APP", name="Application")
        provider = Mock()
        provider.spec = ProviderSpec(
            name="fake",
            label="FakeTracker",
            config_fields=(
                ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),
            ),
        )
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "P1"

        selected = await _choose_project(wizard, cast(Provider, provider), [project], {})

        self.assertEqual(project, selected)
        wizard.choose.assert_awaited_once()
        self.assertEqual("Confirm FakeTracker project", wizard.choose.await_args.kwargs["title"])
        self.assertTrue(wizard.choose.await_args.kwargs["allow_back"])
        [choice] = wizard.choose.await_args.args[0]
        self.assertEqual("only accessible", choice.note)

    async def test_exact_configured_project_is_preselected_but_can_be_changed(self) -> None:
        configured = RemoteProject(project_id="P2", key="OPS", name="Operations")
        other = RemoteProject(project_id="P1", key="APP", name="Application")
        provider = Mock()
        provider.spec = ProviderSpec(
            name="fake",
            label="FakeTracker",
            config_fields=(
                ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),
            ),
        )
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "P1"

        selected = await _choose_project(
            wizard,
            cast(Provider, provider),
            [other, configured],
            {"project_id": "P2"},
        )

        self.assertEqual(other, selected)
        choices = wizard.choose.await_args.args[0]
        self.assertEqual("P2", choices[0].value)
        self.assertEqual("configured", choices[0].note)

    async def test_ambiguous_configured_name_does_not_guess(self) -> None:
        projects = [
            RemoteProject(project_id="P1", key="APP", name="Application"),
            RemoteProject(project_id="P2", key="APP", name="Application"),
        ]
        provider = Mock()
        provider.spec = ProviderSpec(
            name="fake",
            label="FakeTracker",
            config_fields=(
                ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),
            ),
        )
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "P2"

        selected = await _choose_project(
            wizard,
            cast(Provider, provider),
            projects,
            {"project_id": "Application"},
        )

        self.assertEqual("P2", selected.project_id)
        self.assertIn("matches more than one", wizard.note.call_args.args[0])
        self.assertTrue(all(choice.note != "configured" for choice in wizard.choose.await_args.args[0]))

    async def test_five_visible_projects_are_all_searchable_and_no_second_api_call_is_made(self) -> None:
        projects = [
            RemoteProject(project_id=str(index), key=f"P{index}", name=f"Project {index}")
            for index in range(5, 0, -1)
        ]
        provider = Mock()
        provider.spec = ProviderSpec(
            name="fake",
            label="FakeTracker",
            config_fields=(ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),),
        )
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "3"

        selected = await _choose_project(wizard, cast(Provider, provider), projects, {})

        self.assertEqual("3", selected.project_id)
        choices = wizard.choose.await_args.args[0]
        self.assertEqual(["1", "2", "3", "4", "5"], [choice.value for choice in choices])
        self.assertTrue(all(choice.keywords for choice in choices))
        provider.list_projects.assert_not_called()

    async def test_duplicate_names_show_distinguishing_id_and_workspace_context(self) -> None:
        projects = [
            RemoteProject(
                project_id="P1",
                key="Roadmap",
                name="Roadmap",
                extra={"workspace_name": "Engineering"},
            ),
            RemoteProject(
                project_id="P2",
                key="Roadmap",
                name="Roadmap",
                extra={"workspace_name": "Operations"},
            ),
        ]
        provider = Mock()
        provider.spec = ProviderSpec(
            name="asana",
            label="Asana",
            config_fields=(ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),),
        )
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "P1"

        await _choose_project(wizard, cast(Provider, provider), projects, {})

        choices = wizard.choose.await_args.args[0]
        self.assertEqual({"P1", "P2"}, {choice.detail for choice in choices})
        self.assertIn("Engineering", choices[0].description)
        self.assertIn("Operations", choices[1].description)

    async def test_back_from_the_single_project_confirmation_is_preserved(self) -> None:
        project = RemoteProject(project_id="P1", key="APP", name="Application")
        provider = Mock()
        provider.spec = ProviderSpec(
            name="fake",
            label="FakeTracker",
            config_fields=(ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),),
        )
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.side_effect = WizardBack()

        with self.assertRaises(WizardBack):
            await _choose_project(wizard, cast(Provider, provider), [project], {})

    async def test_stale_configured_project_falls_back_to_visible_projects(self) -> None:
        project = RemoteProject(project_id="P1", key="APP", name="Application")
        provider = Mock()
        provider.spec = ProviderSpec(
            name="fake",
            label="FakeTracker",
            config_fields=(
                ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),
            ),
        )
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "P1"

        selected = await _choose_project(
            wizard,
            cast(Provider, provider),
            [project],
            {"project_id": "STALE"},
        )

        wizard.note.assert_called_once()
        self.assertIn("STALE", wizard.note.call_args.args[0])
        self.assertEqual(project, selected)

    async def test_connection_failure_offers_reauthentication_inside_the_wizard(self) -> None:
        wizard = AsyncMock(spec=InitWizardApp)
        wizard.choose.return_value = "retry"

        retry = await _retry_authentication(wizard, "GitHub", ProviderError("credential rejected"))

        self.assertTrue(retry)
        self.assertEqual("Could not connect to GitHub", wizard.choose.await_args.kwargs["title"])
        rendered = repr(wizard.choose.await_args.args[0])
        self.assertIn("Enter credentials again", rendered)
        self.assertIn("credential rejected", rendered)

    async def test_folder_picker_recovers_a_missed_terminal_resize(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            return await wizard.choose_folder(Path.cwd(), title="Where should it live?")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, FolderPicker)

            with patch("pykantui.tui.terminal.current_terminal_size", return_value=Size(170, 46)):
                app._poll_terminal_size()
            # Textual coalesces resize events on a 1/120-second timer.
            await pilot.pause(0.05)

            self.assertEqual((170, 46), (app.screen.region.width, app.screen.region.height))
            dialog = app.screen.query_one("#folder-dialog")
            self.assertAlmostEqual(85, dialog.region.x + dialog.region.width / 2, delta=1)
            self.assertAlmostEqual(23, dialog.region.y + dialog.region.height / 2, delta=1)

    def test_animated_logo_builds_then_decodes_without_changing_dimensions(self) -> None:
        static = _styled_intro(100)
        signal = _styled_intro(100, frame=0)
        assembly = _styled_intro(100, frame=12)
        decode = _styled_intro(100, frame=24)
        settled = _styled_intro(100, frame=40)

        expected_widths = [len(line) for line in static.plain.splitlines()]
        for frame in (signal, assembly, decode, settled):
            self.assertEqual(expected_widths, [len(line) for line in frame.plain.splitlines()])
        self.assertLess(_ink(signal.plain), _ink(assembly.plain))
        self.assertLess(_ink(assembly.plain), _ink(decode.plain))
        self.assertNotEqual(decode.plain, static.plain)
        self.assertEqual(static.plain, settled.plain)

    def test_loader_uses_a_compact_single_wordmark(self) -> None:
        intro = _styled_intro(100)
        lines = intro.plain.splitlines()

        self.assertEqual(9, len(lines))
        logo_width = max(cell_len(line) for line in lines[:-1])
        self.assertGreaterEqual(logo_width, 68)
        self.assertLessEqual(logo_width, 76)

    def test_loader_wordmark_has_two_depth_layers(self) -> None:
        intro = _styled_intro(100)

        self.assertIn("▒", intro.plain)
        self.assertIn("░", intro.plain)

    def test_loader_wordmark_uses_thick_filled_letter_faces(self) -> None:
        intro = _styled_intro(100)

        self.assertGreaterEqual(intro.plain.count("█"), 50)

    def test_scanline_highlights_one_contiguous_logo_row(self) -> None:
        intro = _styled_intro(100, frame=24)
        cyan_spans = [span for span in intro.spans if "7BFFFF" in str(span.style)]

        self.assertTrue(any(span.end - span.start >= 20 for span in cyan_spans))

    def test_intro_stage_describes_each_build_phase(self) -> None:
        self.assertEqual("Acquiring signal", _intro_stage(0))
        self.assertEqual("Assembling glyph matrix", _intro_stage(12))
        self.assertEqual("Decoding PYKANTUI", _intro_stage(24))
        self.assertEqual("Local-first board online", _intro_stage(40))

    def test_build_animation_is_deterministic_and_width_safe(self) -> None:
        logo = _styled_intro(100).plain.rsplit("\n", maxsplit=1)[0]

        for frame in (0, 7, 8, 19, 20, 31, 32, 40, 49):
            animated = _animate_logo_text(logo, frame)
            self.assertEqual(animated, _animate_logo_text(logo, frame))
            self.assertEqual([len(line) for line in logo.splitlines()], [len(line) for line in animated.splitlines()])

    def test_sync_pulse_moves_then_settles_as_ready(self) -> None:
        first = _sync_rail(0)
        second = _sync_rail(1)
        ready = _sync_rail(40)

        self.assertEqual(cell_len(first.plain), cell_len(second.plain))
        self.assertEqual(cell_len(first.plain), cell_len(ready.plain))
        self.assertEqual(39, cell_len(ready.plain))
        self.assertNotEqual(first.plain, second.plain)
        self.assertIn("LOCAL", first.plain)
        self.assertIn("PROVIDER", first.plain)
        self.assertNotIn("READY", first.plain)
        self.assertNotIn("LINKING", first.plain)
        self.assertIn("✓ READY", ready.plain)

    def test_progress_rail_fills_without_changing_width(self) -> None:
        signal = _progress_rail(0)
        next_signal = _progress_rail(1)
        assembly = _progress_rail(12)
        decode = _progress_rail(24)
        ready = _progress_rail(40)

        self.assertEqual({cell_len(value.plain) for value in (signal, next_signal, assembly, decode, ready)}, {37})
        self.assertLess(signal.plain.count("█"), assembly.plain.count("█"))
        self.assertLess(assembly.plain.count("█"), decode.plain.count("█"))
        self.assertNotEqual(signal.plain, next_signal.plain)
        self.assertIn("▒", assembly.plain)
        self.assertIn("▓", assembly.plain)
        self.assertTrue(signal.plain.startswith("▏"))
        self.assertTrue(ready.plain.endswith("100%"))
        self.assertEqual("▏" + "█ " * 15 + "▏ 100%", ready.plain)

    def test_completed_progress_rail_shimmers_without_moving(self) -> None:
        first = _progress_rail(40)
        second = _progress_rail(41)

        self.assertEqual(first.plain, second.plain)
        self.assertNotEqual(first.spans, second.spans)
        self.assertTrue(any("7BFFFF" in str(span.style) for span in first.spans))

    def test_stage_marker_animates_then_settles_as_ready(self) -> None:
        first = _styled_stage(0)
        second = _styled_stage(1)
        ready = _styled_stage(40)

        self.assertNotEqual(first.plain, second.plain)
        self.assertIn(_intro_stage(0), first.plain)
        self.assertEqual("✓ Local-first board online", ready.plain)

    async def test_escape_skips_the_intro_without_cancelling_setup(self) -> None:
        journey = AsyncMock(return_value=None)
        app = InitWizardApp(journey, intro_duration=30)

        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            journey.assert_not_awaited()
            await pilot.press("escape")
            await pilot.pause()

        journey.assert_awaited_once_with(app)

    async def test_intro_uses_clean_topbar_without_default_footer(self) -> None:
        journey = AsyncMock(return_value=None)
        app = InitWizardApp(journey, intro_duration=30)

        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertEqual(1, len(app.query("#wizard-topbar")))
            self.assertEqual(0, len(app.screen.query(Footer)))
            title = app.query_one("#wizard-title", Static)
            self.assertGreater(title.content_region.height, 0)
            self.assertEqual("pykantui - setup", str(title.content))
            await pilot.press("escape")
            await pilot.pause()

    async def test_provider_choice_and_loading_share_one_app_session(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            wizard.loading("Connecting to Jira")
            chosen = await wizard.choose(
                [Choice(value="jira", label="Jira"), Choice(value="linear", label="Linear")],
                title="Which tracker?",
            )
            wizard.done("Connected")
            return Path(f"/{chosen}") if chosen else None

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, Chooser)
            self.assertIn("Connecting to Jira", str(app.screen_stack[0].query_one("#wizard-log", Static).content))
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(Path("/jira"), app.return_value)

    async def test_secret_prompt_masks_the_credential(self) -> None:
        async def journey(wizard: InitWizardApp) -> Path | None:
            token = await wizard.prompt("API token", secret=True)
            return Path(f"/{token}") if token else None

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, WizardPrompt)
            field = app.screen.query_one("#wizard-input", Input)
            self.assertTrue(field.password)
            await pilot.click("#wizard-input")
            await pilot.press("s", "e", "k", "r", "i", "t", "enter")
            await pilot.pause()

        self.assertEqual(Path("/sekrit"), app.return_value)

    async def test_provider_failure_stays_in_the_tui_until_acknowledged(self) -> None:
        async def journey(_wizard: InitWizardApp) -> Path | None:
            raise ProviderError("token rejected", hint="Create a fresh token.")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, WizardMessage)
            body = str(app.screen.query_one("#wizard-message-body", Static).content)
            self.assertIn("token rejected", body)
            self.assertIn("Create a fresh token", body)
            await pilot.press("enter")
            await pilot.pause()

        self.assertIsNone(app.return_value)

    async def test_unexpected_failure_stays_in_the_tui_instead_of_returning_to_the_shell(self) -> None:
        async def journey(_wizard: InitWizardApp) -> Path | None:
            raise RuntimeError("provider response could not be decoded")

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, WizardMessage)
            body = str(app.screen.query_one("#wizard-message-body", Static).content)
            self.assertIn("unexpected error", body.casefold())
            self.assertNotIn("provider response could not be decoded", body)
            await pilot.press("enter")
            await pilot.pause()

        self.assertIsNone(app.return_value)

    async def test_default_intro_animates_for_five_seconds_before_the_journey(self) -> None:
        journey = AsyncMock(return_value=None)

        with patch("pykantui.pages.init_wizard._wait_intro", new=AsyncMock()) as sleep:
            app = InitWizardApp(journey)
            async with app.run_test(size=(100, 38)) as pilot:
                await pilot.pause()

        self.assertEqual(5.0, INTRO_DURATION_SECONDS)
        sleep.assert_awaited_once_with(INTRO_DURATION_SECONDS)
        journey.assert_awaited_once_with(app)


if __name__ == "__main__":
    unittest.main()
