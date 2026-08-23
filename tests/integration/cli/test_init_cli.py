"""``kbn init`` and ``kbn sync``, and the project file they read and write.

No network: a fake provider is registered under a real name for the duration
of each test, so the CLI path is exercised exactly as it would be with Jira.
"""

from __future__ import annotations

import io
import json
import os
import secrets as secret_values
import subprocess
import tempfile
import time
import unittest
from collections.abc import Iterator
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx

from pykantui.api import TTL_STRUCTURE, JsonHttp
from pykantui.cli.main import build_parser, main
from pykantui.config import BoardConfig, cache_path
from pykantui.config import env as dotenv
from pykantui.tracker import register, unregister
from pykantui.tracker.base import Provider
from pykantui.tracker.models import (
    ColumnGroup,
    IssueComponent,
    IssueEdit,
    IssueType,
    RemoteColumn,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.spec import Capabilities, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace import layout
from pykantui.workspace.cache import workspace_cache
from pykantui.workspace.project import Project, load_secrets, resolve_fields, save_secrets
from pykantui.workspace.registry import load_registry
from pykantui.workspace.sync import SyncReport

TODO = RemoteColumn(column_id="1", name="To Do", group=ColumnGroup.TODO)
DONE = RemoteColumn(column_id="2", name="Done", group=ColumnGroup.DONE)
PROJECT = RemoteProject(project_id="P1", key="DEMO", name="Demo project")


class FakeProvider(Provider):
    spec = ProviderSpec(
        name="faketracker",
        label="FakeTracker",
        token_url="https://example.com/tokens",
        auth_fields=(
            ProviderField(name="token", label="API token", kind=FieldKind.SECRET, env_vars=("FAKETRACKER_TOKEN",)),
        ),
        config_fields=(ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),),
        capabilities=Capabilities(move_issues=True, writable_fields=("title", "body", "column_id")),
    )

    projects: list[RemoteProject] = [PROJECT]

    def verify(self) -> RemoteUser:
        if not self.secrets.get("token"):
            from pykantui.tracker.errors import AuthError

            raise AuthError("no token")
        return RemoteUser(display_name="tester", email="t@example.com")

    def list_projects(self) -> list[RemoteProject]:
        return list(self.projects)

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return [TODO, DONE]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(
            [
                RemoteIssue(issue_id="1", key="D-1", title="First", column_id=TODO.column_id, status="To Do"),
                RemoteIssue(issue_id="2", key="D-2", title="Second", column_id=DONE.column_id, status="Done"),
            ]
        )

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        register("faketracker", lambda: FakeProvider, replace=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "board"

        # keep auth.json out of the real user's data directory
        self._home = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"PYKANTUI_HOME": self._home.name}, clear=False)
        self._env.start()
        FakeProvider.projects = [PROJECT]

    def tearDown(self) -> None:
        self._env.stop()
        self._home.cleanup()
        self._tmp.cleanup()
        unregister("faketracker")

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(list(argv))
        return code, buffer.getvalue()


class ListTypesTests(CliCase):
    def test_init_saves_the_requested_theme_before_opening_any_picker(self) -> None:
        code, _ = self.run_cli("--theme", "cyberpunk", "init", "--list-types")

        self.assertEqual(0, code)
        self.assertEqual("cyberpunk", BoardConfig.load().theme)

    def test_every_provider_is_listed(self) -> None:
        code, out = self.run_cli("init", "--list-types")
        self.assertEqual(0, code)
        for name in ("jira", "plane", "github", "linear", "trello"):
            self.assertIn(name, out)

    def test_unverified_providers_are_marked(self) -> None:
        """A draft field mapping should say so before someone trusts it.

        Derived from each spec rather than naming today's providers: an earlier
        version asserted that trello was starred, and started failing the hour
        trello was verified -- reporting a success as a defect.
        """
        from pykantui.tracker import specs

        _, out = self.run_cli("init", "--list-types")
        self.assertIn("not yet verified", out)

        for spec in specs():
            line = next(line for line in out.splitlines() if f" {spec.name} " in line)
            starred = line.startswith("*")
            self.assertEqual(
                not spec.verified,
                starred,
                f"{spec.name} is {'verified' if spec.verified else 'unverified'} but "
                f"{'is' if starred else 'is not'} starred",
            )


class InitTests(CliCase):
    def test_init_registers_the_provider_project_and_selected_workspace(self) -> None:
        code, _ = self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )

        self.assertEqual(0, code)
        [link] = load_registry().projects
        self.assertEqual("faketracker", link.provider)
        self.assertEqual("P1", link.project_id)
        self.assertEqual(self.ws.resolve(), link.workspace_path)

    def test_captured_interactive_init_skips_the_visual_splash_delay(self) -> None:
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=False),
            patch("pykantui.commands.init_interactive.InitWizardApp") as app_type,
        ):
            app_type.return_value.run.return_value = None
            self.run_cli("init")

        self.assertEqual(0.0, app_type.call_args.kwargs["intro_duration"])

    def test_terminal_interactive_init_uses_the_five_second_splash(self) -> None:
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("pykantui.commands.init_interactive.InitWizardApp") as app_type,
        ):
            app_type.return_value.run.return_value = None
            from pykantui.commands.init_interactive import run_interactive

            run_interactive(build_parser().parse_args(["init"]))

        self.assertEqual(5.0, app_type.call_args.kwargs["intro_duration"])

    def test_interactive_init_delegates_the_whole_journey_to_one_tui(self) -> None:
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("pykantui.commands.init_interactive.run_interactive", return_value=0) as interactive,
            patch("pykantui.pages.chooser.choose") as old_chooser,
            patch("pykantui.pages.folder.choose") as old_folder,
        ):
            code, _ = self.run_cli("init")

        self.assertEqual(0, code)
        interactive.assert_called_once()
        old_chooser.assert_not_called()
        old_folder.assert_not_called()

    def test_noninteractive_init_does_not_replace_the_calling_process(self) -> None:
        with patch("pykantui.commands.init._open_workspace") as opened:
            code, _out = self.run_cli(
                "init",
                "--type",
                "faketracker",
                "--path",
                str(self.ws),
                "--token",
                "t",
                "--project-id",
                "P1",
                "--yes",
                "--no-git",
            )

        self.assertEqual(0, code)
        opened.assert_not_called()
        self.assertTrue(layout.project_file(self.ws).is_file())

    def test_interactive_init_prints_nothing_before_the_full_screen_handoff(self) -> None:
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("pykantui.commands.init_interactive.run_interactive", return_value=0) as interactive,
        ):
            code, out = self.run_cli("init")

        self.assertEqual(0, code)
        self.assertEqual("", out)
        interactive.assert_called_once()

    def test_no_open_finishes_without_dumping_the_wizard_into_the_shell(self) -> None:
        code, out = self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
            "--no-open",
        )

        self.assertEqual(0, code)
        self.assertNotIn("\x1b[", out)
        self.assertTrue(layout.project_file(self.ws).is_file())

    def test_interactive_no_sync_stays_full_screen_and_writes_no_cards(self) -> None:
        with patch("pykantui.commands.init._open_workspace"):
            code, out = self.run_cli(
                "init",
                "--type",
                "faketracker",
                "--path",
                str(self.ws),
                "--token",
                "t",
                "--project-id",
                "P1",
                "--yes",
                "--no-git",
                "--no-sync",
            )

        self.assertEqual(0, code)
        self.assertNotIn("\x1b[", out)
        provider_tree = self.ws / "faketracker"
        self.assertEqual([], list(provider_tree.rglob("*.md")) if provider_tree.exists() else [])

    def test_open_workspace_replaces_init_with_the_board_process(self) -> None:
        from pykantui.commands.init import _open_workspace

        with (
            patch("pykantui.commands.init.os.chdir") as chdir,
            patch("pykantui.commands.init._reattach_controlling_terminal") as reattach,
            patch("pykantui.commands.init.os.execv") as execute,
        ):
            _open_workspace(self.ws)

        chdir.assert_called_once_with(self.ws)
        reattach.assert_called_once_with()
        executable, argv = execute.call_args.args
        self.assertEqual(unittest.mock.ANY, executable)
        self.assertEqual([executable, "-m", "pykantui"], argv)

    def test_posix_terminal_handoff_reopens_all_standard_streams(self) -> None:
        from pykantui.commands.init import _reattach_controlling_terminal

        with (
            patch("pykantui.commands.init.os.name", "posix"),
            patch("pykantui.commands.init.os.open", return_value=7) as opened,
            patch("pykantui.commands.init.os.dup2") as duplicate,
            patch("pykantui.commands.init.os.close") as closed,
        ):
            _reattach_controlling_terminal()

        opened.assert_called_once()
        self.assertEqual(
            [unittest.mock.call(7, 0), unittest.mock.call(7, 1), unittest.mock.call(7, 2)],
            duplicate.call_args_list,
        )
        closed.assert_called_once_with(7)

    def test_it_creates_a_workspace_and_pulls(self) -> None:
        code, out = self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )
        self.assertEqual(0, code, out)
        self.assertTrue(layout.project_file(self.ws).is_file())
        self.assertTrue((self.ws / "faketracker/projects/DEMO/to-do/D-1.md").is_file())
        self.assertTrue((self.ws / "faketracker/projects/DEMO/done/D-2.md").is_file())

    def test_the_token_never_lands_in_project_json(self) -> None:
        """The whole point of the config/secret split."""
        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "sekrit",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )
        text = layout.project_file(self.ws).read_text(encoding="utf-8")
        self.assertNotIn("sekrit", text)
        self.assertEqual("sekrit", load_secrets("faketracker")["token"])

    def test_a_bad_token_writes_nothing(self) -> None:
        """A folder scaffolded for a connection that never worked is worse than none."""
        code, _ = self.run_cli(
            "init", "--type", "faketracker", "--path", str(self.ws), "--project-id", "P1", "--yes", "--no-git"
        )
        self.assertEqual(2, code)
        self.assertFalse(self.ws.exists(), "a workspace was created despite the failure")

    def test_it_refuses_to_overwrite_an_existing_workspace(self) -> None:
        args = (
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )
        self.assertEqual(0, self.run_cli(*args)[0])
        code, _ = self.run_cli(*args)
        self.assertEqual(2, code)

    def test_an_unknown_tracker_lists_the_real_ones(self) -> None:
        code, _ = self.run_cli("init", "--type", "jjira", "--path", str(self.ws), "--yes")
        self.assertEqual(2, code)

    def test_a_missing_required_field_names_the_flag(self) -> None:
        code, _ = self.run_cli("init", "--type", "faketracker", "--path", str(self.ws), "--yes")
        self.assertEqual(2, code)

    def test_several_projects_without_a_choice_is_refused(self) -> None:
        """Guessing which project you meant is worse than asking."""
        FakeProvider.projects = [PROJECT, RemoteProject(project_id="P2", key="OTHER")]
        code, _ = self.run_cli(
            "init", "--type", "faketracker", "--path", str(self.ws), "--token", "t", "--yes", "--no-git"
        )
        self.assertEqual(2, code)

    def test_a_single_project_needs_no_choice(self) -> None:
        code, out = self.run_cli(
            "init", "--type", "faketracker", "--path", str(self.ws), "--token", "t", "--yes", "--no-git"
        )
        self.assertEqual(0, code, out)

    def test_single_discovered_project_is_pinned_in_workspace_config(self) -> None:
        code, out = self.run_cli(
            "init", "--type", "faketracker", "--path", str(self.ws), "--token", "t", "--yes", "--no-git"
        )

        self.assertEqual(0, code, out)
        self.assertEqual("P1", Project.load(self.ws).config["project_id"])

    def test_noninteractive_stale_project_is_refused_instead_of_invented(self) -> None:
        code, _out = self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "NOT-VISIBLE",
            "--yes",
            "--no-git",
        )

        self.assertEqual(2, code)
        self.assertFalse(self.ws.exists())

    def test_noninteractive_ambiguous_name_is_refused_instead_of_using_first(self) -> None:
        FakeProvider.projects = [
            RemoteProject(project_id="P1", key="APP", name="Application"),
            RemoteProject(project_id="P2", key="OPS", name="Application"),
        ]

        code, _out = self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "Application",
            "--yes",
            "--no-git",
        )

        self.assertEqual(2, code)
        self.assertFalse(self.ws.exists())

    def test_the_token_can_come_from_the_environment(self) -> None:
        with patch.dict(os.environ, {"FAKETRACKER_TOKEN": "from-env"}):
            code, _ = self.run_cli(
                "init", "--type", "faketracker", "--path", str(self.ws), "--project-id", "P1", "--yes", "--no-git"
            )
        self.assertEqual(0, code)
        self.assertEqual("from-env", load_secrets("faketracker")["token"])

    def test_column_style_is_recorded_not_assumed(self) -> None:
        """So changing the default later cannot orphan an existing tree."""
        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
            "--columns",
            "name",
        )
        self.assertEqual("name", Project.load(self.ws).column_style.value)
        self.assertTrue((self.ws / "faketracker/projects/DEMO/To Do/D-1.md").is_file())

    def test_no_sync_sets_up_without_pulling(self) -> None:
        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
            "--no-sync",
        )
        self.assertTrue(layout.project_file(self.ws).is_file())
        self.assertEqual(
            [], list((self.ws / "faketracker").rglob("*.md")) if (self.ws / "faketracker").exists() else []
        )

    def test_the_cache_is_gitignored(self) -> None:
        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )
        self.assertIn("cache", (self.ws / ".gitignore").read_text(encoding="utf-8"))

    def test_workspace_secret_files_are_gitignored(self) -> None:
        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )

        ignored = (self.ws / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignored)
        self.assertIn(".env*", ignored)
        self.assertIn("auth.json", ignored)
        self.assertIn("credentials.json", ignored)
        self.assertIn(".pykantui/*.lock*", ignored)

    def test_init_preserves_existing_readme_and_merges_gitignore(self) -> None:
        self.ws.mkdir()
        readme = self.ws / "README.md"
        readme.write_text("# My existing notes\n", encoding="utf-8")
        ignore = self.ws / ".gitignore"
        ignore.write_text("private.pem\n", encoding="utf-8")

        code, out = self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )

        self.assertEqual(0, code, out)
        self.assertEqual("# My existing notes\n", readme.read_text(encoding="utf-8"))
        merged = ignore.read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, merged.count("private.pem"))
        self.assertEqual(1, merged.count(".env*"))
        self.assertIn(".pykantui/cache/", merged)

    @unittest.skipUnless(subprocess.run(["git", "--version"], capture_output=True).returncode == 0, "no Git")
    def test_init_commits_only_files_owned_by_pykantui(self) -> None:
        self.ws.mkdir()
        (self.ws / "README.md").write_text("private project notes\n", encoding="utf-8")
        (self.ws / "customer-notes.txt").write_text("do not stage me\n", encoding="utf-8")
        (self.ws / ".env.production").write_text("TOKEN=not-a-real-token\n", encoding="utf-8")

        code, out = self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
        )

        self.assertEqual(0, code, out)
        tracked = subprocess.run(
            ["git", "-C", str(self.ws), "ls-tree", "-r", "--name-only", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertIn(".pykantui/project.json", tracked)
        self.assertTrue(any(path.endswith("D-1.md") for path in tracked), tracked)
        self.assertNotIn("README.md", tracked)
        self.assertNotIn("customer-notes.txt", tracked)
        self.assertNotIn(".env.production", tracked)


class SyncTests(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )

    def test_a_second_sync_reports_no_changes(self) -> None:
        code, out = self.run_cli("sync", "--path", str(self.ws), "--yes", "--no-commit")
        self.assertEqual(0, code)
        self.assertIn("no changes", out)

    def test_dry_run_sends_nothing_and_keeps_the_edit(self) -> None:
        path = self.ws / "faketracker/projects/DEMO/to-do/D-1.md"
        path.write_text(path.read_text(encoding="utf-8").replace("title: First", "title: Edited"), encoding="utf-8")

        code, out = self.run_cli("sync", "--path", str(self.ws), "--dry-run", "--no-commit")
        self.assertEqual(0, code)
        self.assertIn("D-1", out)
        self.assertIn("nothing sent", out)
        self.assertIn("title: Edited", path.read_text(encoding="utf-8"))

    def test_pull_only_never_pushes(self) -> None:
        path = self.ws / "faketracker/projects/DEMO/to-do/D-1.md"
        path.write_text(path.read_text(encoding="utf-8").replace("title: First", "title: Edited"), encoding="utf-8")
        code, out = self.run_cli("sync", "--path", str(self.ws), "--pull-only", "--no-commit")
        self.assertEqual(0, code)
        self.assertIn("title: Edited", path.read_text(encoding="utf-8"))

    def test_outside_a_workspace_it_says_so(self) -> None:
        code, _ = self.run_cli("sync", "--path", str(self.root / "nowhere"), "--yes")
        self.assertEqual(2, code)

    def test_the_cache_summary_is_reported(self) -> None:
        _, out = self.run_cli("sync", "--path", str(self.ws), "--yes", "--no-commit")
        self.assertIn("cache:", out)

    def test_accept_provider_conflicts_is_forwarded_to_the_sync_engine(self) -> None:
        with patch("pykantui.commands.sync.sync_module.sync", return_value=SyncReport()) as run_sync:
            code, _ = self.run_cli(
                "sync",
                "--path",
                str(self.ws),
                "--yes",
                "--no-commit",
                "--accept-provider-conflicts",
            )

        self.assertEqual(0, code)
        self.assertTrue(run_sync.call_args.kwargs["accept_remote_conflicts"])

    def test_retry_ambiguous_comments_is_forwarded_to_the_sync_engine(self) -> None:
        with patch("pykantui.commands.sync.sync_module.sync", return_value=SyncReport()) as run_sync:
            code, _ = self.run_cli(
                "sync",
                "--path",
                str(self.ws),
                "--yes",
                "--no-commit",
                "--retry-ambiguous-comments",
            )

        self.assertEqual(0, code)
        self.assertTrue(run_sync.call_args.kwargs["retry_ambiguous_comments"])


class ProjectFileTests(CliCase):
    def test_new_type_lookup_uses_the_global_workspace_scoped_cache(self) -> None:
        project = Project(provider="faketracker", project_id="P1", key="DEMO", name="Demo")
        layout.meta_dir(self.ws).mkdir(parents=True)
        project.save(self.ws)
        provider = FakeProvider({}, {"token": "test-token"})

        with patch.object(Project, "open", return_value=provider):
            code, _ = self.run_cli("new", "--path", str(self.ws), "--types")

        self.assertEqual(0, code)
        self.assertIsNotNone(provider.cache)
        assert provider.cache is not None
        self.assertEqual(cache_path(), provider.cache.root)
        self.assertEqual("faketracker", provider.cache.provider)
        expected = workspace_cache(
            self.ws,
            "faketracker",
            project.remote(),
            credentials=provider.secrets,
        )
        self.assertEqual(expected.project, provider.cache.project)

    def test_new_types_revalidates_a_six_hour_entry_with_304(self) -> None:
        project = Project(provider="faketracker", project_id="P1", key="DEMO", name="Demo")
        layout.meta_dir(self.ws).mkdir(parents=True)
        project.save(self.ws)
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.headers.get("If-None-Match") == 'W/"types-1"':
                return httpx.Response(304, request=request)
            return httpx.Response(
                200,
                json=[{"id": "1", "name": "Story"}],
                headers={"ETag": 'W/"types-1"'},
                request=request,
            )

        def provider() -> FakeProvider:
            instance = FakeProvider({}, {"token": "test-token"})
            client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond))
            instance._http = JsonHttp("https://example.test", client=client)

            def list_types(project_id: str) -> list[IssueType]:
                assert instance._http is not None
                raw = instance._http.get("/types", ttl=TTL_STRUCTURE, label="issue types")
                assert isinstance(raw, list)
                return [IssueType(type_id=str(item["id"]), name=str(item["name"])) for item in raw]

            instance.list_issue_types = list_types  # type: ignore[method-assign]
            return instance

        with patch.object(Project, "open", return_value=provider()):
            first_code, first_out = self.run_cli("new", "--path", str(self.ws), "--types")

        cache_file = next(cache_path().rglob("issue types-*.json"))
        document = json.loads(cache_file.read_text(encoding="utf-8"))
        document["fetched_at"] = time.time() - TTL_STRUCTURE - 1
        cache_file.write_text(json.dumps(document), encoding="utf-8")

        with patch.object(Project, "open", return_value=provider()):
            second_code, second_out = self.run_cli("new", "--path", str(self.ws), "--types")

        self.assertEqual((0, 0), (first_code, second_code))
        self.assertIn("Story", first_out)
        self.assertIn("Story", second_out)
        self.assertEqual(2, len(requests))
        self.assertEqual('W/"types-1"', requests[1].headers["If-None-Match"])

    def test_new_components_lists_cached_project_components(self) -> None:
        project = Project(provider="faketracker", project_id="P1", key="DEMO", name="Demo")
        layout.meta_dir(self.ws).mkdir(parents=True)
        project.save(self.ws)
        provider = FakeProvider({}, {"token": "test-token"})
        provider.list_components = lambda project_id: [  # type: ignore[method-assign]
            IssueComponent(component_id="1", name="API", description="Backend surface"),
            IssueComponent(component_id="2", name="Platform"),
        ]

        with patch.object(Project, "open", return_value=provider):
            code, out = self.run_cli("new", "--path", str(self.ws), "--components")

        self.assertEqual(0, code)
        self.assertIn("API", out)
        self.assertIn("Platform", out)
        self.assertIsNotNone(provider.cache)

    def test_round_trip(self) -> None:
        project = Project(provider="faketracker", project_id="P1", key="DEMO", name="Demo")
        layout.meta_dir(self.ws).mkdir(parents=True)
        project.save(self.ws)
        self.assertEqual("DEMO", Project.load(self.ws).key)

    def test_a_foreign_schema_is_refused_rather_than_misread(self) -> None:
        layout.meta_dir(self.ws).mkdir(parents=True)
        layout.project_file(self.ws).write_text(json.dumps({"schema": 999}), encoding="utf-8")
        from pykantui.tracker import ProviderError

        with self.assertRaises(ProviderError):
            Project.load(self.ws)

    def test_a_missing_file_points_at_kbn_init(self) -> None:
        from pykantui.tracker import ProviderError

        with self.assertRaises(ProviderError) as caught:
            Project.load(self.ws)
        self.assertIn("kbn init", str(caught.exception))

    def test_saving_one_provider_does_not_drop_another(self) -> None:
        save_secrets("faketracker", {"token": "a"})
        save_secrets("other", {"token": "b"})
        self.assertEqual("a", load_secrets("faketracker")["token"])
        self.assertEqual("b", load_secrets("other")["token"])

    def test_supplied_values_beat_the_environment(self) -> None:
        with patch.dict(os.environ, {"FAKETRACKER_TOKEN": "from-env"}):
            _, secrets = resolve_fields("faketracker", {"token": "explicit"})
        self.assertEqual("explicit", secrets["token"])

    def test_config_and_secrets_are_split_by_the_spec(self) -> None:
        config, secrets = resolve_fields("faketracker", {"token": "t", "project_id": "P1"})
        self.assertEqual({"project_id": "P1"}, config)
        self.assertEqual({"token": "t"}, secrets)

    def test_a_dynamic_provider_can_prompt_for_its_origin_before_loading_secrets(self) -> None:
        """A first Jira setup must reach the wizard before an origin exists."""
        with patch.dict(
            os.environ,
            {
                "JIRA_BASE_URL": "",
                "JIRA_EMAIL": "",
                "JIRA_TOKEN": "",
                "JIRA_API_TOKEN": "",
            },
        ):
            config, secrets = resolve_fields("jira", {})

        self.assertNotIn("base_url", config)
        self.assertEqual({}, secrets)

    def test_workspace_dotenv_origin_cannot_capture_an_exported_credential(self) -> None:
        """A checked-out workspace URL must not inherit a process credential."""
        exported_credential = secret_values.token_urlsafe(24)
        exported_identity = f"{secret_values.token_hex(8)}@example.invalid"
        with patch.dict(
            os.environ,
            {
                "JIRA_EMAIL": exported_identity,
                "JIRA_TOKEN": exported_credential,
            },
        ):
            for name in ("JIRA_BASE_URL", "JIRA_API_TOKEN"):
                os.environ.pop(name, None)
            dotenv.apply({"JIRA_BASE_URL": "https://workspace.invalid"})
            config, resolved = resolve_fields("jira", {})

        self.assertEqual("https://workspace.invalid", config["base_url"])
        self.assertNotIn("email", config)
        self.assertNotIn("token", resolved)

    def test_one_dotenv_may_supply_its_own_origin_and_credential(self) -> None:
        """A deliberate self-contained local configuration remains supported."""
        file_credential = secret_values.token_urlsafe(24)
        with patch.dict(os.environ):
            for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_TOKEN", "JIRA_API_TOKEN"):
                os.environ.pop(name, None)
            dotenv.apply(
                {
                    "JIRA_BASE_URL": "https://workspace.invalid",
                    "JIRA_TOKEN": file_credential,
                }
            )
            _, resolved = resolve_fields("jira", {})

        self.assertTrue(secret_values.compare_digest(file_credential, resolved.get("token", "")))

    def test_existing_workspace_dotenv_origin_cannot_capture_exported_credential(self) -> None:
        exported_credential = secret_values.token_urlsafe(24)
        project = Project(
            provider="jira",
            project_id="APP",
            config={"base_url": "https://workspace.invalid"},
        )
        with patch.dict(os.environ, {"JIRA_TOKEN": exported_credential}):
            for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
                os.environ.pop(name, None)
            dotenv.apply({"JIRA_BASE_URL": "https://workspace.invalid"})
            resolved = project.secrets()

        self.assertNotIn("token", resolved)

    def test_saved_credentials_are_bound_to_the_exact_provider_origin(self) -> None:
        """A cloned workspace cannot redirect a saved token to another host."""
        official = {"base_url": "https://api.github.com", "repo": "owner/repo"}
        attacker = {"base_url": "https://attacker.example", "repo": "owner/repo"}
        save_secrets("github", {"token": "saved-secret"}, config=official)

        trusted = Project(provider="github", project_id="owner/repo", config=official)
        untrusted = Project(provider="github", project_id="owner/repo", config=attacker)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "GITHUB_API_URL": ""}):
            self.assertEqual("saved-secret", trusted.secrets()["token"])
            self.assertNotIn("token", untrusted.secrets())

    def test_environment_credentials_need_a_matching_custom_origin(self) -> None:
        project = Project(
            provider="github",
            project_id="owner/repo",
            config={"base_url": "https://github.corp.example/api/v3", "repo": "owner/repo"},
        )

        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "environment-secret", "GH_TOKEN": "", "GITHUB_API_URL": ""},
        ):
            self.assertNotIn("token", project.secrets())

        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "environment-secret",
                "GH_TOKEN": "",
                "GITHUB_API_URL": "https://github.corp.example/api/v3",
            },
        ):
            self.assertEqual("environment-secret", project.secrets()["token"])

    def test_insecure_provider_origin_is_rejected_before_credentials_are_loaded(self) -> None:
        project = Project(
            provider="github",
            project_id="owner/repo",
            config={"base_url": "http://attacker.example", "repo": "owner/repo"},
        )

        from pykantui.tracker import ProviderError

        with self.assertRaisesRegex(ProviderError, "HTTPS"):
            project.open()

    def test_undeclared_provider_config_cannot_smuggle_a_base_url(self) -> None:
        project = Project(
            provider="asana",
            project_id="123",
            config={"base_url": "https://attacker.example", "project_gid": "123"},
        )

        from pykantui.tracker import ProviderError

        with self.assertRaisesRegex(ProviderError, "unknown setting"):
            project.open()


if __name__ == "__main__":
    unittest.main()


class ContextualBoardTests(CliCase):
    """Bare `kbn` reads the room, the way `git` does."""

    def test_outside_a_workspace_there_is_none(self) -> None:
        from pykantui.cli.main import _workspace_backend

        with patch("pykantui.workspace.layout.find_workspace", return_value=None):
            self.assertIsNone(_workspace_backend())

    def test_inside_a_workspace_it_opens_that_board(self) -> None:
        from pykantui.cli.main import _workspace_backend
        from pykantui.sync.provider import ProviderBackend

        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )

        with patch("pykantui.workspace.layout.find_workspace", return_value=self.ws):
            backend = _workspace_backend()
        self.assertIsInstance(backend, ProviderBackend)
        assert backend is not None
        self.assertEqual(["To Do", "Done"], [c.name for c in backend.get_columns()])
        self.assertEqual({"D-1", "D-2"}, {t.metadata["key"] for t in backend.get_tasks()})

    def test_the_board_reads_files_not_the_network(self) -> None:
        """Opening a board must be instant and must work offline."""
        from pykantui.cli.main import _workspace_backend

        self.run_cli(
            "init",
            "--type",
            "faketracker",
            "--path",
            str(self.ws),
            "--token",
            "t",
            "--project-id",
            "P1",
            "--yes",
            "--no-git",
        )

        calls: list[str] = []

        def counted(self_: object, project_id: str) -> Iterator[RemoteIssue]:
            calls.append(project_id)
            return iter(())

        with (
            patch.object(FakeProvider, "iter_issues", counted),
            patch("pykantui.workspace.layout.find_workspace", return_value=self.ws),
        ):
            backend = _workspace_backend()
            assert backend is not None
            backend.get_tasks()
        self.assertEqual([], calls, "opening the board fetched issues from the tracker")

    def test_a_broken_workspace_is_reported_not_silently_replaced(self) -> None:
        """Opening an empty JSON board instead would hide the real problem."""
        from pykantui.cli.main import _workspace_backend

        layout.meta_dir(self.ws).mkdir(parents=True)
        layout.project_file(self.ws).write_text('{"schema": 999}', encoding="utf-8")

        with patch("pykantui.workspace.layout.find_workspace", return_value=self.ws), self.assertRaises(SystemExit):
            _workspace_backend()

    def test_an_explicit_file_still_means_the_json_board(self) -> None:
        """There has to be a way to say 'the local board' from inside a workspace."""
        import argparse

        from pykantui.cli.main import _backend_for
        from pykantui.sync.jsonstore import JsonBackend

        local = self.root / "local.json"
        args = argparse.Namespace(file=local)
        with patch("pykantui.workspace.layout.find_workspace", return_value=self.ws):
            self.assertIsInstance(_backend_for("board", args), JsonBackend)


class EnvFileTests(unittest.TestCase):
    """`.env` is where credentials live; the CLI has to actually read it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_reads_key_value_pairs(self) -> None:
        from pykantui.config import env

        (self.root / ".env").write_text(
            "# a comment\n\nJIRA_EMAIL=a@b.c\nJIRA_TOKEN='quoted'\nexport PLANE_TOKEN=\"exported\"\n",
            encoding="utf-8",
        )
        values = env.read(self.root / ".env")
        self.assertEqual({"JIRA_EMAIL": "a@b.c", "JIRA_TOKEN": "quoted", "PLANE_TOKEN": "exported"}, values)

    def test_a_real_environment_variable_wins(self) -> None:
        """A .env is a convenience; an export is a deliberate act."""
        from pykantui.config import env

        with patch.dict(os.environ, {"JIRA_TOKEN": "from-shell"}):
            env.apply({"JIRA_TOKEN": "from-file"})
            self.assertEqual("from-shell", os.environ["JIRA_TOKEN"])

    def test_it_is_found_from_a_subdirectory(self) -> None:
        from pykantui.config import env

        (self.root / ".env").write_text("JIRA_TOKEN=yes\n", encoding="utf-8")
        deep = self.root / "a" / "b"
        deep.mkdir(parents=True)
        os.environ.pop("JIRA_TOKEN", None)
        try:
            self.assertEqual(self.root / ".env", env.load(deep))
            self.assertEqual("yes", os.environ["JIRA_TOKEN"])
        finally:
            os.environ.pop("JIRA_TOKEN", None)

    def test_no_env_file_is_not_an_error(self) -> None:
        from pykantui.config import env

        self.assertIsNone(env.load(self.root, depth=0))

    def test_malformed_lines_are_skipped_not_fatal(self) -> None:
        from pykantui.config import env

        (self.root / ".env").write_text("garbage\n=novalue\nGOOD=1\n", encoding="utf-8")
        self.assertEqual({"GOOD": "1"}, env.read(self.root / ".env"))


class ListIdsTests(unittest.TestCase):
    """`--list-ids` closes the chicken-and-egg in .env.

    The board id lives behind the API, the API needs .env filled in, and the
    only thing that listed ids was the interactive wizard -- which you cannot
    reach until .env is filled in. Several of these ids are also invisible in
    the tracker's own web UI, so "look it up in the app" is not an answer
    either.
    """

    def run_cli(self, *argv: str) -> tuple[int, str]:
        import contextlib
        import io

        from pykantui.cli.main import main

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["init", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_without_a_type_it_says_so(self) -> None:
        code, text = self.run_cli("--list-ids")

        self.assertEqual(2, code)
        self.assertIn("--type", text)
        self.assertNotIn("Traceback", text)

    def test_without_credentials_it_names_the_env_vars(self) -> None:
        """The whole point is telling you what to put in .env.

        The credentials are cleared explicitly: this passed or failed depending
        on whether the machine running it happened to have Trello configured,
        which is not a property of the code.
        """
        import os
        from unittest import mock

        cleared = dict.fromkeys(("TRELLO_KEY", "TRELLO_API_KEY", "TRELLO_TOKEN"), "")
        with mock.patch.dict(os.environ, cleared, clear=False):
            code, text = self.run_cli("--type", "trello", "--list-ids")

        self.assertEqual(2, code)
        self.assertIn("TRELLO_KEY", text)
        self.assertIn("TRELLO_TOKEN", text)
        self.assertNotIn("Traceback", text)

    def test_an_unknown_tracker_is_rejected(self) -> None:
        code, text = self.run_cli("--type", "nosuchtracker", "--list-ids")

        self.assertEqual(2, code)
        self.assertNotIn("Traceback", text)
