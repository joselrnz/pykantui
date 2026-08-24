"""Provider integrations enter through one registry and one workspace backend."""

from __future__ import annotations

import argparse
import importlib.util
import unittest
from pathlib import Path

from pykantui.cli.main import build_parser
from pykantui.tracker import names
from pykantui.workspace.sync import PendingPush, SyncPlan, SyncReport


class ProviderArchitectureTests(unittest.TestCase):
    def test_sync_data_models_are_not_owned_by_the_orchestrator(self) -> None:
        for model in (PendingPush, SyncPlan, SyncReport):
            with self.subTest(model=model.__name__):
                self.assertEqual("pykantui.workspace.models", model.__module__)

    def test_no_provider_has_a_top_level_special_case_command(self) -> None:
        parser = build_parser()
        subcommands = next(
            action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        )

        self.assertNotIn("jira", subcommands)
        self.assertIn("init", subcommands)
        self.assertIn("sync", subcommands)

    def test_legacy_jira_sdk_layers_are_gone(self) -> None:
        self.assertIsNone(importlib.util.find_spec("pykantui.api.jira"))
        self.assertIsNone(importlib.util.find_spec("pykantui.sync.jira"))

    def test_registry_exposes_every_provider_to_the_same_init_flow(self) -> None:
        self.assertEqual(
            {
                "asana",
                "clickup",
                "forgejo",
                "github",
                "jira",
                "linear",
                "monday",
                "plane",
                "shortcut",
                "trello",
            },
            set(names()),
        )

    def test_graphql_operations_are_separate_from_provider_orchestration(self) -> None:
        package_root = Path(__file__).parents[3] / "src" / "pykantui" / "providers"
        for provider in ("linear", "monday"):
            with self.subTest(provider=provider):
                root = package_root / provider
                for module in (
                    "client.py",
                    "operations.py",
                    "schemas.py",
                    "mapper.py",
                    "payloads.py",
                    "provider.py",
                ):
                    self.assertTrue((root / module).is_file(), f"{provider} is missing {module}")
                orchestration = (root / "provider.py").read_text(encoding="utf-8")
                self.assertNotIn("query (", orchestration)
                self.assertNotIn("mutation (", orchestration)
                self.assertNotIn("self.http.graphql(", orchestration)

    def test_migrated_rest_providers_separate_transport_mapping_and_payloads(self) -> None:
        package_root = Path(__file__).parents[3] / "src" / "pykantui" / "providers"
        for provider in ("asana", "clickup", "forgejo", "github", "jira", "plane", "shortcut", "trello"):
            with self.subTest(provider=provider):
                root = package_root / provider
                for module in ("client.py", "routes.py", "schemas.py", "mapper.py", "payloads.py", "provider.py"):
                    self.assertTrue((root / module).is_file(), f"{provider} is missing {module}")
                orchestration = (root / "provider.py").read_text(encoding="utf-8")
                for raw_call in (
                    "self.http.get(",
                    "self.http.post(",
                    "self.http.put(",
                    "self.http.patch(",
                    "self.http.request(",
                ):
                    self.assertNotIn(raw_call, orchestration)

    def test_workspace_sync_is_a_small_coordinator(self) -> None:
        workspace_root = Path(__file__).parents[3] / "src" / "pykantui" / "workspace"
        sync_lines = (workspace_root / "sync.py").read_text(encoding="utf-8").splitlines()
        self.assertLess(len(sync_lines), 350)
        for module in ("disk.py", "outbound.py", "planner.py"):
            with self.subTest(module=module):
                self.assertTrue((workspace_root / module).is_file())

    def test_tui_menu_data_is_not_embedded_in_the_textual_app(self) -> None:
        tui_root = Path(__file__).parents[3] / "src" / "pykantui" / "tui"
        app_source = (tui_root / "app.py").read_text(encoding="utf-8")

        self.assertTrue((tui_root / "menu_items.py").is_file())
        self.assertNotIn("case Menu.MAIN", app_source)
        self.assertLess(len(app_source.splitlines()), 1_000)


if __name__ == "__main__":
    unittest.main()
