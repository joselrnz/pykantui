"""Reading a ``.env``, including the shapes people actually write.

This file is hand-edited by someone pasting tokens in, so it collects every
malformed and half-formed line a person produces at 11pm. The parser's job is
to be unsurprising about all of them -- and above all never to hand back a
value that is really a comment, because that lands in a base URL and fails
somewhere far from the file that caused it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pykantui.config import env


class ReadTests(unittest.TestCase):
    def parse(self, text: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / ".env"
            path.write_text(text, encoding="utf-8")
            return env.read(path)

    # ---- the ordinary shapes --------------------------------------------

    def test_a_plain_pair(self) -> None:
        self.assertEqual({"A": "1"}, self.parse("A=1"))

    def test_blank_lines_and_comments_are_skipped(self) -> None:
        self.assertEqual({"A": "1"}, self.parse("\n# a note\n\nA=1\n\n"))

    def test_whitespace_around_the_name_and_value(self) -> None:
        self.assertEqual({"A": "1"}, self.parse("  A =  1  "))

    def test_export_prefix(self) -> None:
        """Common in files people also source from a shell."""
        self.assertEqual({"A": "1"}, self.parse("export A=1"))

    def test_quotes_are_stripped(self) -> None:
        self.assertEqual({"A": "1", "B": "2"}, self.parse("A='1'\nB=\"2\""))

    def test_an_empty_value(self) -> None:
        self.assertEqual({"A": ""}, self.parse("A="))

    def test_the_last_definition_wins(self) -> None:
        """Matches shell behaviour, and is what someone re-pasting expects."""
        self.assertEqual({"A": "2"}, self.parse("A=1\nA=2"))

    # ---- the shapes that used to break ----------------------------------

    def test_a_trailing_comment_is_not_the_value(self) -> None:
        """An optional URL followed by a comment must remain empty."""
        self.assertEqual({"A": "https://x"}, self.parse("A=https://x  # the host"))

    def test_a_value_that_is_only_a_comment_is_empty(self) -> None:
        self.assertEqual({"A": ""}, self.parse("A=   # optional"))

    def test_a_hash_inside_a_token_survives(self) -> None:
        """Truncating a credential would be worse than the bug this fixes."""
        self.assertEqual({"A": "tok#en"}, self.parse("A=tok#en"))

    def test_a_hash_inside_a_quoted_value_survives(self) -> None:
        self.assertEqual({"A": "tok # en"}, self.parse('A="tok # en"'))

    # ---- awkward but legal ----------------------------------------------

    def test_an_equals_sign_inside_the_value(self) -> None:
        """Base64 and JWTs end in '='; splitting on the last one would corrupt them."""
        self.assertEqual({"A": "a=b=c"}, self.parse("A=a=b=c"))

    def test_a_jwt_shaped_value(self) -> None:
        token = "eyJhbGciOiJIUzI1NiJ9.eyJhIjoxfQ.c2ln=="
        self.assertEqual({"A": token}, self.parse(f"A={token}"))

    def test_a_line_with_no_equals_is_ignored(self) -> None:
        self.assertEqual({"A": "1"}, self.parse("nonsense\nA=1"))

    def test_a_nameless_line_is_ignored(self) -> None:
        self.assertEqual({}, self.parse("=1"))

    def test_crlf_line_endings(self) -> None:
        """Windows editors, and files that have been through git with autocrlf."""
        self.assertEqual({"A": "1", "B": "2"}, self.parse("A=1\r\nB=2\r\n"))

    def test_a_utf8_bom_does_not_break_the_first_line(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / ".env"
            path.write_bytes("﻿A=1\nB=2\n".encode())
            found = env.read(path)

        self.assertEqual("2", found.get("B"))
        self.assertIn("1", found.values(), "the first line was lost to the BOM")

    def test_a_unicode_value(self) -> None:
        self.assertEqual({"A": "café ☕"}, self.parse("A=café ☕"))

    def test_a_missing_file_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            self.assertEqual({}, env.read(Path(name) / "nope.env"))

    def test_an_unreadable_file_is_not_an_error(self) -> None:
        """A directory where a file was expected, say."""
        with tempfile.TemporaryDirectory() as name:
            self.assertEqual({}, env.read(Path(name)))


class ApplyTests(unittest.TestCase):
    def test_the_real_environment_wins(self) -> None:
        """A .env is a default, not an override -- CI sets the real thing."""
        with mock.patch.dict(os.environ, {"JIRA_TOKEN": "real"}, clear=False):
            env.apply({"JIRA_TOKEN": "from-file"})
            self.assertEqual("real", os.environ["JIRA_TOKEN"])

    def test_an_unset_variable_is_filled_in(self) -> None:
        os.environ.pop("JIRA_TOKEN", None)
        try:
            env.apply({"JIRA_TOKEN": "from-file"})
            self.assertEqual("from-file", os.environ["JIRA_TOKEN"])
        finally:
            os.environ.pop("JIRA_TOKEN", None)

    def test_workspace_env_cannot_inject_process_control_variables(self) -> None:
        names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PYKANTUI_HOME")
        previous = {name: os.environ.pop(name, None) for name in names}
        try:
            env.apply({name: "attacker-controlled" for name in names})
            self.assertTrue(all(name not in os.environ for name in names))
        finally:
            for name, value in previous.items():
                if value is not None:
                    os.environ[name] = value


class LoadTests(unittest.TestCase):
    def test_it_walks_up_to_find_the_file(self) -> None:
        """Running kbn from a subdirectory should still see the project's .env."""
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / ".env").write_text("JIRA_TOKEN=yes\n", encoding="utf-8")
            deep = root / "a" / "b"
            deep.mkdir(parents=True)

            os.environ.pop("JIRA_TOKEN", None)
            try:
                found = env.load(deep)
                self.assertIsNotNone(found)
                self.assertEqual("yes", os.environ.get("JIRA_TOKEN"))
            finally:
                os.environ.pop("JIRA_TOKEN", None)

    def test_it_gives_up_rather_than_walking_to_the_root(self) -> None:
        """Otherwise a stray .env in a home directory leaks into every project."""
        with tempfile.TemporaryDirectory() as name:
            deep = Path(name) / "a" / "b" / "c" / "d" / "e"
            deep.mkdir(parents=True)

            self.assertIsNone(env.load(deep, depth=2))


class ExampleFileTests(unittest.TestCase):
    """`.env.example` is committed, so it must never carry a real value."""

    def example(self) -> Path:
        return Path(__file__).resolve().parents[3] / ".env.example"

    def test_it_exists(self) -> None:
        self.assertTrue(self.example().is_file())

    def test_every_variable_is_empty(self) -> None:
        """A committed sample with a live token in it is the classic leak."""
        values = env.read(self.example())

        filled = {name: value for name, value in values.items() if value.strip()}
        self.assertEqual({}, filled, "a value escaped into the committed sample")

    def test_it_covers_every_provider(self) -> None:
        """Generated from the specs, so a new provider cannot be forgotten."""
        from pykantui.providers import builtin_providers
        from pykantui.tracker import get

        text = self.example().read_text(encoding="utf-8")
        for name in builtin_providers():
            spec = get(name).spec
            for field in (*spec.auth_fields, *spec.config_fields):
                if field.env_vars:
                    self.assertIn(field.env_vars[0], text, f"{name}.{field.name} is undocumented")

    def test_it_names_the_discovery_command(self) -> None:
        """The id fields are useless without it."""
        self.assertIn("--list-ids", self.example().read_text(encoding="utf-8"))

    def test_it_is_plain_ascii(self) -> None:
        """A .env is sometimes sourced by a shell with an unhelpful locale."""
        text = self.example().read_text(encoding="utf-8")

        offenders = sorted({ch for ch in text if ord(ch) > 127})
        self.assertEqual([], offenders, f"non-ascii in .env.example: {offenders}")


if __name__ == "__main__":
    unittest.main()
