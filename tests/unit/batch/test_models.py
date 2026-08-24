"""Declarative batch manifest parsing and dependency rules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pykantui.batch.models import BatchManifest, load_manifest, write_generated_manifest
from pykantui.tracker.errors import ProviderError


class ManifestTests(unittest.TestCase):
    def test_generator_writes_ten_explicit_stable_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "issues.yml"
            write_generated_manifest(path, provider="jira", count=10)
            manifest = load_manifest(path)
            generated = path.read_text(encoding="utf-8")

        self.assertEqual(manifest.target.provider, "jira")
        self.assertEqual(len(manifest.issues), 10)
        self.assertEqual(manifest.issues[0].ref, "issue-01")
        self.assertEqual(manifest.issues[-1].ref, "issue-10")
        self.assertTrue(all(issue.title is None for issue in manifest.issues))
        self.assertEqual(generated.count("title: null"), 10)
        self.assertEqual(generated.count("body: null"), 10)

    def test_parent_dependencies_are_topologically_sorted(self) -> None:
        manifest = BatchManifest.model_validate(
            {
                "apiVersion": "pykantui.dev/v1alpha1",
                "kind": "IssueBatch",
                "metadata": {"name": "release"},
                "target": {"provider": "jira"},
                "issues": [
                    {"ref": "child", "title": "Child", "type": "Sub-task", "parent": "parent"},
                    {"ref": "parent", "title": "Parent", "type": "Story"},
                ],
            }
        )

        self.assertEqual([item.ref for item in manifest.ordered_issues()], ["parent", "child"])

    def test_dependency_cycles_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            BatchManifest.model_validate(
                {
                    "apiVersion": "pykantui.dev/v1alpha1",
                    "kind": "IssueBatch",
                    "metadata": {"name": "cycle"},
                    "target": {"provider": "jira"},
                    "issues": [
                        {"ref": "one", "title": "One", "parent": "two"},
                        {"ref": "two", "title": "Two", "parent": "one"},
                    ],
                }
            )

    def test_unknown_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "extra"):
            BatchManifest.model_validate(
                {
                    "apiVersion": "pykantui.dev/v1alpha1",
                    "kind": "IssueBatch",
                    "metadata": {"name": "strict"},
                    "target": {"provider": "jira"},
                    "issues": [{"ref": "one", "title": "One", "surprise": True}],
                }
            )

    def test_yaml_aliases_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "issues.yml"
            path.write_text(
                "apiVersion: pykantui.dev/v1alpha1\n"
                "kind: IssueBatch\n"
                "metadata: {name: aliases}\n"
                "target: {provider: jira}\n"
                "issues:\n"
                "  - &base {ref: one, title: One}\n"
                "  - *base\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ProviderError, "aliases"):
                load_manifest(path)

    def test_duplicate_yaml_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "issues.yml"
            path.write_text(
                "apiVersion: pykantui.dev/v1alpha1\n"
                "kind: IssueBatch\n"
                "metadata: {name: duplicate}\n"
                "target: {provider: jira}\n"
                "target: {provider: forgejo}\n"
                "issues: []\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ProviderError, "duplicate key"):
                load_manifest(path)


if __name__ == "__main__":
    unittest.main()
