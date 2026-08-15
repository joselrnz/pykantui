"""Conformance tests for ``docs/markdown-format.md``.

The format is a contract between pykantui, your editor and git. A spec nobody
checks is a wish, so every rule in that document has a test here, and the file
names the rule it is enforcing.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from pykantui.tracker.models import RemoteIssue
from pykantui.workspace import markdown

SPEC = Path(__file__).resolve().parents[3] / "docs" / "markdown-format.md"

CENTRAL = timezone(timedelta(hours=-5))

FULL = RemoteIssue(
    issue_id="10018",
    key="JPT-4",
    title="Task 1",
    column_id="10009",
    status="In Progress",
    issue_type="Task",
    priority="High",
    assignee="alex",
    reporter="sam",
    labels=("backend", "urgent"),
    parent_key="JPT-1",
    created_at=datetime(2026, 8, 7, 20, 56, 5, 516000, tzinfo=CENTRAL),
    updated_at=datetime(2026, 8, 7, 20, 57, 10, tzinfo=CENTRAL),
    due_date=date(2026, 8, 9),
    url="https://acme.atlassian.net/browse/JPT-4",
    body="The description as the tracker holds it.",
)


def render(issue: RemoteIssue = FULL, **kw: object) -> str:
    options: dict[str, object] = {"column_name": "in-progress", "provider": "jira"}
    options.update(kw)
    return markdown.render(issue, **options)  # type: ignore[arg-type]


def front(text: str) -> dict[str, object]:
    block = text.split("---", 2)[1]
    loaded = yaml.safe_load(block)
    assert isinstance(loaded, dict)
    return loaded


class SpecExistsTests(unittest.TestCase):
    def test_the_spec_is_in_the_repository(self) -> None:
        """These tests enforce a document; the document has to be there."""
        self.assertTrue(SPEC.is_file(), f"missing {SPEC}")

    def test_the_spec_documents_both_markers(self) -> None:
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("pykantui:source", text)
        self.assertIn("pykantui:notes", text)


class FrontmatterOrderTests(unittest.TestCase):
    """ "Order is fixed... identity, then state, then people, then dates, links." """

    def test_fields_appear_in_the_documented_order(self) -> None:
        expected = [
            "key",
            "id",
            "provider",
            "title",
            "status",
            "column",
            "type",
            "priority",
            "assignee",
            "reporter",
            "labels",
            "parent",
            "created",
            "updated",
            "due",
            "url",
        ]
        block = render().split("---", 2)[1]
        actual = [line.split(":", 1)[0] for line in block.strip().splitlines() if ":" in line]
        self.assertEqual(expected, actual)

    def test_order_is_stable_when_fields_are_missing(self) -> None:
        bare = RemoteIssue(issue_id="1", key="K-1", title="T")
        block = render(bare).split("---", 2)[1]
        actual = [line.split(":", 1)[0] for line in block.strip().splitlines() if ":" in line]
        self.assertEqual(["key", "id", "provider", "title", "column"], actual)


class FrontmatterTypeTests(unittest.TestCase):
    """ "id ... always quoted ... 007 must not become 7." """

    def test_the_id_is_always_a_quoted_string(self) -> None:
        self.assertIn('id: "10018"', render())

    def test_a_leading_zero_id_survives_the_round_trip(self) -> None:
        """The failure this prevents: an id that changes type stops matching."""
        issue = FULL.model_copy(update={"issue_id": "007"})
        parsed = markdown.parse(render(issue))
        self.assertEqual("007", parsed.front["id"])
        self.assertIsInstance(parsed.front["id"], str)

    def test_timestamps_are_iso_8601_with_a_T(self) -> None:
        """PyYAML's native timestamp writes a space and microseconds."""
        values = front(render())
        self.assertEqual("2026-08-07T20:56:05-05:00", values["created"])
        self.assertEqual("2026-08-07T20:57:10-05:00", values["updated"])
        for name in ("created", "updated"):
            self.assertIsInstance(values[name], str, f"{name} became a YAML timestamp")

    def test_the_due_date_is_a_plain_day(self) -> None:
        self.assertIn("due: '2026-08-09'", render() + render())

    def test_labels_are_a_flow_list_on_one_line(self) -> None:
        self.assertIn("labels: [backend, urgent]", render())

    def test_a_long_url_is_never_wrapped(self) -> None:
        """A wrapped URL is a broken URL when something naive reads the file."""
        long_url = "https://example.atlassian.net/browse/" + ("A" * 200)
        text = render(FULL.model_copy(update={"url": long_url}))
        self.assertIn(long_url, text)
        self.assertEqual(long_url, front(text)["url"])


class EmptyFieldTests(unittest.TestCase):
    """ "Empty fields are omitted." """

    def test_absent_values_produce_no_key(self) -> None:
        bare = RemoteIssue(issue_id="1", key="K-1", title="T")
        text = render(bare)
        for name in ("priority", "assignee", "reporter", "labels", "parent", "due", "url", "type"):
            self.assertNotIn(f"{name}:", text, f"{name} was written with no value")

    def test_the_id_is_written_even_though_it_is_never_empty(self) -> None:
        self.assertIn("id:", render(RemoteIssue(issue_id="1")))


class RegionTests(unittest.TestCase):
    """ "Three regions, and who owns each." """

    def test_all_three_regions_are_present(self) -> None:
        text = render()
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("# JPT-4 · Task 1", text)
        self.assertIn("pykantui:source", text)
        self.assertIn("pykantui:notes", text)

    def test_the_heading_is_key_then_title(self) -> None:
        self.assertIn("# JPT-4 · Task 1", render())

    def test_a_titleless_issue_gets_a_bare_heading(self) -> None:
        text = render(RemoteIssue(issue_id="1", key="K-1"))
        self.assertIn("# K-1", text)
        self.assertNotIn("# K-1 ·", text)

    def test_a_draft_has_no_key_line(self) -> None:
        """An unsent story has no key, and must not borrow its local id as one."""
        text = render(RemoteIssue(issue_id="draft-port-the-picker", key="", title="Port it"))
        self.assertNotIn("key:", text)
        self.assertIn('id: "draft-port-the-picker"', text)

    def test_a_draft_heading_is_just_the_title(self) -> None:
        text = render(RemoteIssue(issue_id="draft-port-the-picker", key="", title="Port it"))
        self.assertIn("# Port it", text)
        self.assertNotIn("draft-port-the-picker ·", text)

    def test_the_heading_is_not_read_back_as_the_body(self) -> None:
        """Otherwise the first sync would push the heading into the description."""
        parsed = markdown.parse(render())
        self.assertNotIn("# JPT-4", parsed.source)
        self.assertEqual("The description as the tracker holds it.", parsed.source)

    def test_notes_are_preserved_and_source_is_not(self) -> None:
        text = render(notes="my own notes")
        parsed = markdown.parse(text)
        self.assertEqual("my own notes", parsed.notes)
        self.assertEqual("The description as the tracker holds it.", parsed.source)


class MarkerTests(unittest.TestCase):
    """ "Markers are matched on their token alone." """

    def test_the_documented_markers_are_emitted(self) -> None:
        text = render()
        self.assertIn("<!-- pykantui:source", text)
        self.assertIn("<!-- pykantui:notes", text)

    def test_an_older_bare_marker_still_parses(self) -> None:
        """A file written before the markers gained their explanatory text."""
        text = "---\nkey: K-1\n---\n\nbody\n\n<!-- pykantui:notes -->\nkeep me\n"
        parsed = markdown.parse(text)
        self.assertEqual("keep me", parsed.notes)
        self.assertEqual("body", parsed.source)

    def test_a_reworded_marker_still_parses(self) -> None:
        text = "---\nkey: K-1\n---\n\nbody\n\n<!-- pykantui:notes anything at all -->\nkeep me\n"
        self.assertEqual("keep me", markdown.parse(text).notes)

    def test_no_notes_marker_means_no_notes(self) -> None:
        """Not 'the whole body is notes', which would freeze it against syncs."""
        parsed = markdown.parse("---\nkey: K-1\n---\n\nbody text\n")
        self.assertEqual("", parsed.notes)
        self.assertEqual("body text", parsed.source)


class RobustnessTests(unittest.TestCase):
    """The guarantees listed under "Robustness rules"."""

    def test_broken_yaml_never_loses_the_notes(self) -> None:
        text = "---\nkey: [unclosed\n---\n\nbody\n\n<!-- pykantui:notes -->\nirreplaceable\n"
        parsed = markdown.parse(text)
        self.assertEqual({}, parsed.front)
        self.assertEqual("irreplaceable", parsed.notes)

    def test_a_file_with_no_frontmatter_still_parses(self) -> None:
        self.assertEqual("hello", markdown.parse("hello\n").source)

    def test_a_multiline_title_is_collapsed(self) -> None:
        """A newline in a YAML scalar would break the block."""
        text = render(FULL.model_copy(update={"title": "one\ntwo"}))
        self.assertEqual("one two", front(text)["title"])

    def test_rendering_twice_is_byte_identical(self) -> None:
        """A sync that found nothing must produce no diff and no commit."""
        self.assertEqual(render(), render())

    def test_a_full_round_trip_changes_nothing(self) -> None:
        """render -> parse -> render must be stable, or every sync churns."""
        once = render(notes="notes stay")
        parsed = markdown.parse(once)
        twice = render(
            FULL.model_copy(update={"body": parsed.source}),
            notes=parsed.notes,
        )
        self.assertEqual(once, twice)

    def test_unicode_survives(self) -> None:
        issue = FULL.model_copy(update={"title": "Customize ⚙️ settings", "body": "emoji 🎯 body"})
        parsed = markdown.parse(render(issue))
        self.assertEqual("Customize ⚙️ settings", parsed.front["title"])
        self.assertIn("🎯", parsed.source)

    def test_a_colon_in_a_title_does_not_break_the_block(self) -> None:
        """The exact case the module docstring cites for using a real parser."""
        issue = FULL.model_copy(update={"title": "Fix: the thing"})
        self.assertEqual("Fix: the thing", front(render(issue))["title"])

    def test_yaml_metacharacters_in_a_title_survive(self) -> None:
        for title in ("- dash start", "#hash", "@at", "*star", "{brace}", "[bracket]", "yes", "null"):
            with self.subTest(title=title):
                self.assertEqual(title, front(render(FULL.model_copy(update={"title": title})))["title"])

    def test_a_body_containing_the_frontmatter_delimiter_is_safe(self) -> None:
        """A description with a `---` line must not truncate the file."""
        issue = FULL.model_copy(update={"body": "before\n---\nafter"})
        parsed = markdown.parse(render(issue))
        self.assertIn("after", parsed.source)

    def test_a_body_containing_the_notes_marker_text(self) -> None:
        """A description mentioning the marker splits at the first one, and the
        real notes are still recoverable below it."""
        issue = FULL.model_copy(update={"body": "see <!-- pykantui:notes --> below"})
        text = render(issue, notes="real notes")
        parsed = markdown.parse(text)
        self.assertIn("real notes", parsed.notes)

    def test_windows_line_endings_parse(self) -> None:
        text = render().replace("\n", "\r\n")
        parsed = markdown.parse(text)
        self.assertEqual("JPT-4", parsed.front["key"])

    def test_the_file_ends_with_exactly_one_newline(self) -> None:
        text = render()
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))


class EditableFieldTests(unittest.TestCase):
    """ "Which fields you may edit." """

    def test_the_documented_editable_keys_are_read_back(self) -> None:
        for name in ("title", "assignee", "labels", "due", "priority"):
            self.assertIn(name, markdown.EDITABLE_KEYS)

    def test_editing_a_documented_field_is_detected(self) -> None:
        parsed = markdown.parse(render())
        parsed.front["title"] = "Renamed"
        parsed.source = FULL.body
        edit = markdown.edit_from(parsed, column_id=FULL.column_id, previous=FULL)
        self.assertEqual(("title",), edit.touched())

    def test_editing_status_by_hand_does_nothing(self) -> None:
        """The folder is the authority; status is informational."""
        parsed = markdown.parse(render())
        parsed.front["status"] = "Done"
        parsed.source = FULL.body
        edit = markdown.edit_from(parsed, column_id=FULL.column_id, previous=FULL)
        self.assertTrue(edit.is_empty(), f"unexpected: {edit.touched()}")


if __name__ == "__main__":
    unittest.main()
