"""The selected locale applies to argparse output without leaking globally."""

from __future__ import annotations

import contextlib
import io
import unittest

from pykantui.cli.main import main
from pykantui.i18n import Locale, current_locale


class LocalizedHelpTests(unittest.TestCase):
    def test_spanish_help_translates_application_owned_text(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--locale", "es", "--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("Tablero kanban para la terminal", output.getvalue())
        self.assertIn("idioma de la interfaz", output.getvalue())

    def test_cli_locale_does_not_leak_after_the_command_finishes(self) -> None:
        before = current_locale()

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            main(["--locale=es", "--help"])

        self.assertIs(current_locale(), before)

    def test_english_remains_available_explicitly(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main(["--locale", "en", "--help"])

        self.assertIn("Terminal kanban board", output.getvalue())
        self.assertIn("interface language", output.getvalue())
        self.assertIn(Locale.AUTO.value, output.getvalue())

    def test_spanish_applies_to_subcommand_help(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--locale", "es", "sync", "--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("reconciliar un espacio de trabajo con su proveedor", output.getvalue())

    def test_french_help_translates_application_owned_text(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--locale", "fr", "sync", "--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("réconcilier un espace de travail avec son fournisseur", output.getvalue())

    def test_german_help_uses_the_german_catalog(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--locale", "de", "--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("Kanban-Board im Terminal", output.getvalue())

    def test_simplified_chinese_help_uses_the_chinese_catalog(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--locale", "zh", "--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("终端看板", output.getvalue())

    def test_traditional_chinese_help_uses_the_taiwanese_catalog(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--locale", "zh_TW", "--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("終端機看板", output.getvalue())

    def test_arabic_help_uses_the_arabic_catalog(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--locale", "ar", "--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("لوحة كانبان في الطرفية", output.getvalue())


if __name__ == "__main__":
    unittest.main()
