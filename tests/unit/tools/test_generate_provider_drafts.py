"""Safety contracts for the provider-aware Markdown batch generator."""

from __future__ import annotations

import unittest

from tools.generate_provider_drafts import draft_numbers, validate_run_tag


class GenerateProviderDraftTests(unittest.TestCase):
    def test_run_tag_is_strict_and_terminal_safe(self) -> None:
        self.assertEqual(
            "PKT-E2E-20260814T122600Z-3bd16524",
            validate_run_tag("PKT-E2E-20260814T122600Z-3bd16524"),
        )
        for unsafe in ("", "../escape", "tag\nowned", "[markup]", "x" * 81):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                validate_run_tag(unsafe)

    def test_resume_is_exact_and_never_overproduces(self) -> None:
        self.assertEqual(tuple(range(1, 21)), draft_numbers(existing=0, wanted=20))
        self.assertEqual(tuple(range(8, 21)), draft_numbers(existing=7, wanted=20))
        self.assertEqual((), draft_numbers(existing=20, wanted=20))
        with self.assertRaises(ValueError):
            draft_numbers(existing=21, wanted=20)

    def test_count_is_bounded(self) -> None:
        for count in (0, -1, 101):
            with self.subTest(count=count), self.assertRaises(ValueError):
                draft_numbers(existing=0, wanted=count)


if __name__ == "__main__":
    unittest.main()
