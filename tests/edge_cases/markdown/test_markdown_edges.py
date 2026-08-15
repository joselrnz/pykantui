"""Markdown round-trips, with the content trackers actually contain.

Every one of these is a data-loss risk rather than a cosmetic one. A title that
does not survive a round-trip becomes a spurious edit, which becomes a push
that overwrites the real title on somebody else's board. A notes block that
does not survive is somebody's private thinking, gone.

So the shape of most tests here is: render it, parse it back, and insist
nothing moved.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from pykantui.tracker.models import RemoteIssue
from pykantui.workspace import markdown

HOSTILE_TITLES = [
    "Plain",
    "With: a colon",  # YAML would read this as a mapping
    "With - a dash",
    "  leading and trailing  ",
    '"Already quoted"',
    "'Single quoted'",
    "With #hash and #tags",  # YAML comment character
    "With | a pipe",  # YAML block scalar
    "With > an angle",  # YAML folded scalar
    "With {braces} and [brackets]",  # YAML flow collections
    "With @at and `backtick`",
    "With \\backslash",
    "100% done",
    "café ☕ 進行中 ✅",  # unicode and emoji
    "x" * 300,  # very long
    "- starts like a list item",
    "* also like a list item",
    "--- like a document break",
    "null",  # YAML would parse as None
    "true",  # ...as a bool
    "12345",  # ...as an int
    "1.0",  # ...as a float
    "2026-08-09",  # ...as a date
    "on",  # ...as a bool in YAML 1.1
]


def issue(**kw: object) -> RemoteIssue:
    base: dict[str, object] = {
        "issue_id": "1",
        "key": "ACME-1",
        "title": "Title",
        "column_id": "c1",
        "status": "To Do",
    }
    base.update(kw)
    return RemoteIssue(**base)  # type: ignore[arg-type]


def roundtrip(source: RemoteIssue, notes: str = "") -> markdown.IssueFile:
    text = markdown.render(source, column_name="to-do", notes=notes, provider="jira")
    return markdown.parse(text)


class TitleTests(unittest.TestCase):
    def test_every_hostile_title_survives(self) -> None:
        for title in HOSTILE_TITLES:
            parsed = roundtrip(issue(title=title))
            expected = " ".join(title.split())  # collapsing is the documented rule
            self.assertEqual(expected, parsed.front.get("title"), f"lost: {title!r}")

    def test_a_newline_in_a_title_is_collapsed_not_dropped(self) -> None:
        """A raw newline would end the YAML scalar and corrupt the block."""
        parsed = roundtrip(issue(title="Line one\nLine two"))

        self.assertEqual("Line one Line two", parsed.front.get("title"))

    def test_a_title_of_only_whitespace(self) -> None:
        parsed = roundtrip(issue(title="   "))

        self.assertIn("title", parsed.front | {"title": ""})


class BodyTests(unittest.TestCase):
    def test_a_body_containing_the_frontmatter_fence(self) -> None:
        """`---` inside a body must not be read as the end of the file."""
        body = "before\n---\nafter"
        parsed = roundtrip(issue(body=body))

        self.assertIn("before", parsed.source)
        self.assertIn("after", parsed.source)

    def test_a_body_containing_the_notes_marker(self) -> None:
        """Otherwise a tracker description could swallow your notes."""
        parsed = roundtrip(issue(body="text <!-- pykantui:notes --> more"), notes="MINE")

        self.assertIn("MINE", parsed.notes)

    def test_an_empty_body(self) -> None:
        self.assertEqual("", roundtrip(issue(body="")).source.strip())

    def test_a_body_of_only_whitespace(self) -> None:
        self.assertEqual("", roundtrip(issue(body="   \n\n  ")).source.strip())

    def test_a_very_long_body(self) -> None:
        body = "\n".join(f"line {n}" for n in range(2000))
        parsed = roundtrip(issue(body=body))

        self.assertIn("line 1999", parsed.source)

    def test_unicode_and_emoji_in_a_body(self) -> None:
        parsed = roundtrip(issue(body="進行中 ✅ café"))

        self.assertIn("進行中", parsed.source)

    def test_windows_line_endings_in_a_body(self) -> None:
        parsed = roundtrip(issue(body="one\r\ntwo"))

        self.assertIn("one", parsed.source)
        self.assertIn("two", parsed.source)


class NotesTests(unittest.TestCase):
    def test_notes_survive_a_round_trip(self) -> None:
        parsed = roundtrip(issue(), notes="private thinking")

        self.assertIn("private thinking", parsed.notes)

    def test_notes_are_not_part_of_the_body(self) -> None:
        """They are never pushed, so they must never leak into `source`."""
        parsed = roundtrip(issue(body="tracker text"), notes="private thinking")

        self.assertNotIn("private thinking", parsed.source)

    def test_empty_notes(self) -> None:
        self.assertEqual("", roundtrip(issue(), notes="").notes.strip())

    def test_notes_containing_a_frontmatter_fence(self) -> None:
        parsed = roundtrip(issue(), notes="a\n---\nb")

        self.assertIn("a", parsed.notes)
        self.assertIn("b", parsed.notes)


class FieldTests(unittest.TestCase):
    def test_labels_with_awkward_characters(self) -> None:
        labels = ("with space", "with,comma", "with:colon", "café", "#hash", "[bracket]")
        parsed = roundtrip(issue(labels=labels))

        self.assertEqual(list(labels), list(parsed.front.get("labels", [])))

    def test_no_labels_means_no_key(self) -> None:
        """An empty list would be indistinguishable from "cleared"."""
        self.assertNotIn("labels", roundtrip(issue(labels=())).front)

    def test_a_single_label(self) -> None:
        self.assertEqual(["one"], list(roundtrip(issue(labels=("one",))).front["labels"]))

    def test_an_assignee_that_looks_like_a_number(self) -> None:
        """Monday and Asana ids are numeric; YAML would make them ints."""
        parsed = roundtrip(issue(assignee="1201234567890123"))

        self.assertEqual("1201234567890123", str(parsed.front.get("assignee")))

    def test_an_id_with_leading_zeros_is_not_an_octal(self) -> None:
        parsed = roundtrip(issue(issue_id="007"))

        self.assertEqual("007", str(parsed.front.get("id")))

    def test_a_due_date_round_trips(self) -> None:
        parsed = roundtrip(issue(due_date=date(2026, 8, 9)))

        self.assertIn("2026-08-09", str(parsed.front.get("due")))

    def test_a_timestamp_round_trips(self) -> None:
        parsed = roundtrip(issue(created_at=datetime(2026, 8, 9, 17, 48, 44)))

        self.assertIn("2026-08-09", str(parsed.front.get("created")))

    def test_a_url_with_a_query_string(self) -> None:
        url = "https://acme.atlassian.net/browse/ACME-1?filter=a&b=c#frag"
        parsed = roundtrip(issue(url=url))

        self.assertEqual(url, parsed.front.get("url"))


class ParseTests(unittest.TestCase):
    """Reading files that were not written by us."""

    def test_a_file_with_no_frontmatter(self) -> None:
        parsed = markdown.parse("just some text\n")

        self.assertEqual({}, parsed.front)

    def test_an_unterminated_frontmatter_block(self) -> None:
        parsed = markdown.parse("---\nkey: A-1\ntitle: x\n")

        self.assertIsInstance(parsed.front, dict)

    def test_an_empty_file(self) -> None:
        parsed = markdown.parse("")

        self.assertEqual({}, parsed.front)
        self.assertEqual("", parsed.source.strip())

    def test_broken_yaml_does_not_raise(self) -> None:
        """A hand-edited file with a typo must not take the whole sync down."""
        parsed = markdown.parse("---\nkey: [unclosed\n---\n\nbody\n")

        self.assertIsInstance(parsed.front, dict)
        self.assertFalse(parsed.valid)
        self.assertIn("YAML", parsed.errors[0])

    def test_duplicate_editable_keys_are_rejected(self) -> None:
        parsed = markdown.parse("---\nid: '1'\ntitle: first\ntitle: second\n---\n")

        self.assertFalse(parsed.valid)
        self.assertIn("duplicate", " ".join(parsed.errors).lower())

    def test_scalar_labels_are_invalid_not_interpreted_as_clear(self) -> None:
        parsed = markdown.parse("---\nid: '1'\ntitle: card\nlabels: security\n---\n")

        self.assertFalse(parsed.valid)
        self.assertIn("labels", " ".join(parsed.errors))

    def test_impossible_due_date_is_invalid_not_interpreted_as_clear(self) -> None:
        parsed = markdown.parse("---\nid: '1'\ntitle: card\ndue: 2026-99-99\n---\n")

        self.assertFalse(parsed.valid)
        self.assertIn("due", " ".join(parsed.errors))

    def test_a_file_with_no_markers_keeps_its_text(self) -> None:
        """Written by an older version, or by hand."""
        parsed = markdown.parse("---\nkey: A-1\n---\n\nSome body text.\n")

        self.assertIn("Some body text.", parsed.source)

    def test_crlf_throughout(self) -> None:
        text = markdown.render(issue(body="b"), column_name="to-do", notes="n", provider="jira")
        parsed = markdown.parse(text.replace("\n", "\r\n"))

        self.assertEqual("ACME-1", parsed.front.get("key"))
        self.assertIn("b", parsed.source)
        self.assertIn("n", parsed.notes)


class StabilityTests(unittest.TestCase):
    """Rendering twice must produce the same bytes, or git churns."""

    def test_render_is_deterministic(self) -> None:
        card = issue(labels=("b", "a"), body="text", assignee="alex@example.com")
        first = markdown.render(card, column_name="to-do", notes="n", provider="jira")
        second = markdown.render(card, column_name="to-do", notes="n", provider="jira")

        self.assertEqual(first, second)

    def test_a_parsed_file_re_renders_identically(self) -> None:
        """The round-trip must be a fixed point, or every sync shows a diff."""
        card = issue(body="text", labels=("a",), assignee="alex@example.com")
        once = markdown.render(card, column_name="to-do", notes="mine", provider="jira")
        parsed = markdown.parse(once)
        twice = markdown.render(card, column_name="to-do", notes=parsed.notes, provider="jira")

        self.assertEqual(once, twice)

    def test_every_hostile_title_is_a_fixed_point(self) -> None:
        for title in HOSTILE_TITLES:
            card = issue(title=" ".join(title.split()))
            once = markdown.render(card, column_name="to-do", provider="jira")
            twice = markdown.render(card, column_name="to-do", notes=markdown.parse(once).notes, provider="jira")
            self.assertEqual(once, twice, f"unstable for {title!r}")


if __name__ == "__main__":
    unittest.main()
