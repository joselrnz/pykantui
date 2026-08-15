"""The interactive CLI presentation stays readable in every output tier."""

from __future__ import annotations

import unittest

from rich.cells import cell_len

from pykantui.cli.presentation import render_intro, render_loader_intro


class IntroTests(unittest.TestCase):
    def test_plain_intro_is_ascii_and_names_the_app(self) -> None:
        intro = render_intro(color=False, width=120)

        intro.encode("ascii")
        self.assertIn("PYKANTUI", intro)
        self.assertNotIn("\x1b[", intro)
        self.assertNotIn("**", intro)
        self.assertGreaterEqual(len(intro.splitlines()), 20)
        self.assertLessEqual(max(map(len, intro.splitlines())), 100)

    def test_narrow_terminal_uses_the_compact_logo_without_wrapping(self) -> None:
        intro = render_intro(color=False, width=64)

        self.assertIn("PYKANTUI", intro)
        self.assertLessEqual(max(map(len, intro.splitlines())), 64)

    def test_cyberpunk_intro_reserves_error_red_for_actual_errors(self) -> None:
        intro = render_intro(color=True, width=120)

        self.assertIn("\x1b[38;2;0;200;255m", intro)
        self.assertNotIn("\x1b[38;2;224;108;117m", intro)
        self.assertTrue(intro.endswith("\x1b[0m"))

    def test_loader_wordmark_falls_back_without_wrapping_narrow_terminals(self) -> None:
        intro = render_loader_intro(width=64)

        self.assertEqual(8, len(intro.splitlines()))
        self.assertIn("▒", intro)
        self.assertIn("░", intro)
        self.assertLessEqual(max(cell_len(line) for line in intro.splitlines()), 64)

if __name__ == "__main__":
    unittest.main()
