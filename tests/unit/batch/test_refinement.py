"""AI refinements remain local, attributable, and unable to replace user input."""

from __future__ import annotations

import unittest

from pykantui.batch.models import BatchManifest, FieldSource
from pykantui.batch.refinement import BatchRefinement, apply_refinement
from pykantui.tracker.errors import ProviderError


def manifest() -> BatchManifest:
    return BatchManifest.model_validate(
        {
            "apiVersion": "pykantui.dev/v1alpha1",
            "kind": "IssueBatch",
            "metadata": {"name": "checkout"},
            "target": {"provider": "jira"},
            "defaults": {"type": "Story"},
            "issues": [
                {"ref": "one", "title": None, "body": None},
                {"ref": "two", "title": "User title", "sources": {"title": "user"}},
            ],
        }
    )


def proposal(*issues: dict[str, object]) -> BatchRefinement:
    return BatchRefinement.model_validate(
        {
            "apiVersion": "pykantui.dev/v1alpha1",
            "kind": "IssueBatchRefinement",
            "batch": "checkout",
            "issues": list(issues),
        }
    )


class RefinementTests(unittest.TestCase):
    def test_fills_only_missing_fields_and_records_ai_provenance(self) -> None:
        refined = apply_refinement(
            manifest(),
            proposal({"ref": "one", "title": "AI title", "body": "Acceptance criteria"}),
        )

        self.assertEqual(refined.issues[0].title, "AI title")
        self.assertEqual(refined.issues[0].sources["title"], FieldSource.AI)
        self.assertEqual(refined.issues[0].sources["body"], FieldSource.AI)

    def test_refuses_to_replace_user_authored_fields(self) -> None:
        with self.assertRaisesRegex(ProviderError, "cannot replace 'title'"):
            apply_refinement(manifest(), proposal({"ref": "two", "title": "AI overwrite"}))

    def test_redo_ai_can_only_replace_prior_ai_fields(self) -> None:
        once = apply_refinement(manifest(), proposal({"ref": "one", "title": "First"}))
        twice = apply_refinement(
            once,
            proposal({"ref": "one", "title": "Second"}),
            redo_ai=True,
        )

        self.assertEqual(twice.issues[0].title, "Second")

    def test_defaults_are_not_silently_overridden(self) -> None:
        with self.assertRaisesRegex(ProviderError, "cannot replace 'type'"):
            apply_refinement(manifest(), proposal({"ref": "one", "type": "Task"}))


if __name__ == "__main__":
    unittest.main()
