"""Every built-in provider satisfies the shared Sync safety contract.

No live account is touched here. The contract checks declarations and concrete
provider methods; HTTP request mapping remains covered by ``test_tracker``.
"""

from __future__ import annotations

import unittest

from pykantui.tracker.base import Provider
from pykantui.tracker.registry import get, specs
from pykantui.tracker.spec import ProviderSpec
from pykantui.workspace.status import SyncStatus

EXPECTED = {
    "asana",
    "clickup",
    "github",
    "jira",
    "linear",
    "monday",
    "plane",
    "shortcut",
    "trello",
}


class EveryProviderSyncContractTests(unittest.TestCase):
    def providers(self) -> list[tuple[ProviderSpec, type[Provider]]]:
        return [(spec, get(spec.name)) for spec in specs()]

    def test_the_matrix_covers_every_built_in_provider(self) -> None:
        self.assertEqual(EXPECTED, {spec.name for spec, _provider in self.providers()})

    def test_every_provider_can_read_columns_and_issues(self) -> None:
        for spec, provider in self.providers():
            with self.subTest(provider=spec.name):
                self.assertFalse(provider.__abstractmethods__)

    def test_every_declared_move_has_a_concrete_implementation(self) -> None:
        for spec, provider in self.providers():
            with self.subTest(provider=spec.name):
                if spec.capabilities.move_issues:
                    self.assertIsNot(provider.move_issue, Provider.move_issue)
                    self.assertIn("column_id", spec.capabilities.writable_fields)

    def test_every_declared_edit_has_a_concrete_implementation(self) -> None:
        for spec, provider in self.providers():
            with self.subTest(provider=spec.name):
                if spec.capabilities.writable_fields:
                    self.assertIsNot(provider.update_issue, Provider.update_issue)

    def test_every_declared_create_has_a_concrete_implementation(self) -> None:
        for spec, provider in self.providers():
            with self.subTest(provider=spec.name):
                if spec.capabilities.create_issues:
                    self.assertIsNot(provider.create_issue, Provider.create_issue)

    def test_every_sync_state_has_a_unique_visible_icon_and_label(self) -> None:
        self.assertEqual(len(SyncStatus), len({status.marker for status in SyncStatus}))
        self.assertEqual(len(SyncStatus), len({status.label for status in SyncStatus}))
        for status in SyncStatus:
            with self.subTest(status=status.value):
                self.assertTrue(status.marker.strip())
                self.assertTrue(status.label.strip())


if __name__ == "__main__":
    unittest.main()
