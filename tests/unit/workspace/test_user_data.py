"""Global installed-package state stays under the user's pykantui home."""

from __future__ import annotations

import json
import os
import secrets as secret_values
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pykantui.config.paths import cache_path, data_dir, migrate_legacy_data, projects_path
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import RemoteProject
from pykantui.workspace.cache import workspace_cache
from pykantui.workspace.project import Project
from pykantui.workspace.registry import load_registry, register_workspace


class GlobalPathTests(unittest.TestCase):
    def test_default_home_is_dot_pykantui_on_every_platform(self) -> None:
        home = Path("C:/people/alex")
        with patch.dict(os.environ, {"PYKANTUI_HOME": ""}, clear=False), patch(
            "pykantui.config.paths.Path.home", return_value=home
        ):
            self.assertEqual(home / ".pykantui", data_dir())

    def test_override_keeps_tests_containers_and_portable_installs_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"PYKANTUI_HOME": directory}, clear=False
        ):
            self.assertEqual(Path(directory).resolve(), data_dir())
            self.assertEqual(Path(directory).resolve() / "cache", cache_path())
            self.assertEqual(Path(directory).resolve() / "projects.json", projects_path())

    def test_legacy_user_json_is_copied_once_without_overwriting_new_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            (old / "auth.json").write_text('{"token": "kept"}', encoding="utf-8")
            (old / "config.json").write_text('{"theme": "old"}', encoding="utf-8")
            (old / "cache").mkdir()
            (new / "config.json").write_text('{"theme": "new"}', encoding="utf-8")

            migrated = migrate_legacy_data(sources=(old,), destination=new)

            self.assertEqual([new / "auth.json"], migrated)
            self.assertEqual('{"token": "kept"}', (new / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual('{"theme": "new"}', (new / "config.json").read_text(encoding="utf-8"))
            self.assertFalse((new / "cache").exists())


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._work = tempfile.TemporaryDirectory()
        self._environment = patch.dict(os.environ, {"PYKANTUI_HOME": self._home.name}, clear=False)
        self._environment.start()
        self.root = Path(self._work.name)

    def tearDown(self) -> None:
        self._environment.stop()
        self._work.cleanup()
        self._home.cleanup()

    @staticmethod
    def project(project_id: str = "P1") -> Project:
        return Project(provider="jira", project_id=project_id, key=project_id, name=f"Project {project_id}")

    def test_register_records_the_selected_absolute_workspace_path(self) -> None:
        workspace = self.root / "boards" / "one"
        workspace.mkdir(parents=True)

        registered = register_workspace(workspace, self.project())

        self.assertEqual(workspace.resolve(), registered.workspace_path)
        self.assertTrue(registered.available)
        self.assertEqual([registered], load_registry().projects)

    def test_registering_the_same_workspace_updates_instead_of_duplicating(self) -> None:
        workspace = self.root / "board"
        workspace.mkdir()
        register_workspace(workspace, self.project("OLD"))

        register_workspace(workspace, self.project("NEW"))

        links = load_registry().projects
        self.assertEqual(1, len(links))
        self.assertEqual("NEW", links[0].project_id)

    def test_same_provider_project_can_have_two_local_workspaces(self) -> None:
        first, second = self.root / "first", self.root / "second"
        first.mkdir()
        second.mkdir()

        register_workspace(first, self.project())
        register_workspace(second, self.project())

        self.assertEqual(
            {first.resolve(), second.resolve()},
            {link.workspace_path for link in load_registry().projects},
        )

    def test_missing_workspace_is_retained_and_marked_unavailable(self) -> None:
        workspace = self.root / "later-moved"
        register_workspace(workspace, self.project())

        [link] = load_registry().projects

        self.assertFalse(link.available)
        self.assertEqual(workspace.resolve(), link.workspace_path)

    def test_registry_never_serializes_project_config_or_credentials(self) -> None:
        workspace = self.root / "safe"
        workspace.mkdir()
        project = self.project()
        project.config = {"base_url": "https://jira.example", "token": "do-not-store"}

        register_workspace(workspace, project)

        raw = projects_path().read_text(encoding="utf-8")
        self.assertNotIn("do-not-store", raw)
        self.assertNotIn("token", raw)
        self.assertNotIn("config", raw)

    def test_corrupt_registry_is_reported_and_never_overwritten(self) -> None:
        projects_path().parent.mkdir(parents=True, exist_ok=True)
        projects_path().write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(ProviderError, "project registry"):
            register_workspace(self.root / "board", self.project())

        self.assertEqual("{broken", projects_path().read_text(encoding="utf-8"))

    def test_unknown_fields_are_rejected_instead_of_becoming_hidden_secrets(self) -> None:
        projects_path().parent.mkdir(parents=True, exist_ok=True)
        projects_path().write_text(
            json.dumps(
                {
                    "schema": 1,
                    "projects": [
                        {
                            "provider": "jira",
                            "project_id": "P1",
                            "key": "P1",
                            "name": "Project",
                            "workspace": str(self.root),
                            "updated_at": "2026-08-12T00:00:00Z",
                            "token": "unexpected",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ProviderError, "project registry"):
            load_registry()

    def test_concurrent_registrations_do_not_lose_projects(self) -> None:
        workspaces = [self.root / f"board-{number}" for number in range(32)]
        for workspace in workspaces:
            workspace.mkdir()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda pair: register_workspace(pair[1], self.project(str(pair[0]))), enumerate(workspaces)))

        self.assertEqual(
            {path.resolve() for path in workspaces},
            {link.workspace_path for link in load_registry().projects},
        )


class GlobalCachePlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._work = tempfile.TemporaryDirectory()
        self._environment = patch.dict(os.environ, {"PYKANTUI_HOME": self._home.name}, clear=False)
        self._environment.start()
        self.project = Project(provider="jira", project_id="100", key="APP", name="App")

    def tearDown(self) -> None:
        self._environment.stop()
        self._work.cleanup()
        self._home.cleanup()

    def test_provider_cache_lives_in_user_home_not_workspace(self) -> None:
        workspace = Path(self._work.name) / "board"
        cache = workspace_cache(workspace, self.project.provider, self.project.remote())

        self.assertEqual(cache_path(), cache.root)
        self.assertEqual("jira", cache.provider)
        self.assertFalse(str(cache.directory()).startswith(str(workspace)))

    def test_two_workspaces_cannot_read_each_others_cached_provider_data(self) -> None:
        first = workspace_cache(Path(self._work.name) / "one", "jira", self.project.remote())
        second = workspace_cache(Path(self._work.name) / "two", "jira", self.project.remote())

        first.put("issues", [{"id": "private-to-one"}])

        self.assertNotEqual(first.project, second.project)
        self.assertIsNone(second.get("issues"))

    def test_same_workspace_and_slug_still_isolate_remote_project_and_account(self) -> None:
        workspace = Path(self._work.name) / "board"
        first_project = RemoteProject(
            project_id="100",
            key="APP",
            owner="acme",
            url="https://one.example.test/projects/APP",
        )
        second_project = first_project.model_copy(
            update={
                "project_id": "200",
                "owner": "other-account",
                "url": "https://two.example.test/projects/APP",
            }
        )

        first = workspace_cache(workspace, "jira", first_project)
        second = workspace_cache(workspace, "jira", second_project)
        first.put("issue-types", [{"id": "private-to-first-account"}])

        self.assertNotEqual(first.project, second.project)
        self.assertIsNone(second.get("issue-types"))

    def test_credential_generations_cannot_read_each_others_cache(self) -> None:
        workspace = Path(self._work.name) / "board"
        first_credential = secret_values.token_urlsafe(24)
        second_credential = secret_values.token_urlsafe(24)
        first = workspace_cache(
            workspace,
            "jira",
            self.project.remote(),
            credentials={"token": first_credential},
        )
        second = workspace_cache(
            workspace,
            "jira",
            self.project.remote(),
            credentials={"token": second_credential},
        )

        first.put("issue-types", [{"id": "private-to-first-login"}])

        self.assertNotEqual(first.project, second.project)
        self.assertIsNone(second.get("issue-types"))
        persisted = " ".join(str(path) for path in cache_path().rglob("*"))
        self.assertFalse(first_credential in persisted)
        self.assertFalse(second_credential in persisted)
        identity_key = (cache_path().parent / ".cache-identity-key").read_bytes()
        self.assertFalse(first_credential.encode() in identity_key)
        self.assertFalse(second_credential.encode() in identity_key)

    @unittest.skipIf(os.name == "nt", "POSIX modes; Windows uses its native ACL behavior")
    def test_cache_home_and_identity_key_are_owner_only(self) -> None:
        workspace_cache(
            Path(self._work.name) / "board",
            "jira",
            self.project.remote(),
            credentials={"token": secret_values.token_urlsafe(24)},
        )

        self.assertEqual(0o700, data_dir().stat().st_mode & 0o777)
        self.assertEqual(0o700, cache_path().stat().st_mode & 0o777)
        identity_key = cache_path().parent / ".cache-identity-key"
        self.assertEqual(0o600, identity_key.stat().st_mode & 0o777)

    def test_cache_identity_key_is_repaired_through_private_file_helper(self) -> None:
        with patch("pykantui.workspace.cache.ensure_private_file") as secure:
            workspace_cache(
                Path(self._work.name) / "board",
                "jira",
                self.project.remote(),
                credentials={"token": secret_values.token_urlsafe(24)},
            )

        secure.assert_called_with(cache_path().parent / ".cache-identity-key")


if __name__ == "__main__":
    unittest.main()
