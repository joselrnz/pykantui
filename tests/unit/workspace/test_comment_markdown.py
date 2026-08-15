"""Markdown contract for provider comments and local append-only drafts."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from pykantui.tracker import models
from pykantui.tracker.models import RemoteIssue
from pykantui.workspace import markdown

ISSUE = RemoteIssue(issue_id="10018", key="JPT-4", title="Commented card", body="Provider description")


def _comment_types() -> tuple[type[Any], type[Any]]:
    remote = getattr(models, "RemoteComment", None)
    draft = getattr(models, "CommentDraft", None)
    if remote is None or draft is None:
        raise AssertionError("tracker.models must define RemoteComment and CommentDraft")
    return remote, draft


def _remote(body: str = "Provider reply") -> Any:
    RemoteComment, _ = _comment_types()
    return RemoteComment(
        comment_id="007",
        issue_id=ISSUE.issue_id,
        body=body,
        author="José",
        author_id="acct-9",
        created_at=datetime(2026, 8, 13, 12, 30, tzinfo=UTC),
        url="https://tracker.example/comments/007",
    )


def _draft(body: str = "Local reply") -> Any:
    _, CommentDraft = _comment_types()
    return CommentDraft(
        local_id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA",
        issue_id=ISSUE.issue_id,
        body=body,
    )


def _render(*, comments: tuple[Any, ...] = (), drafts: tuple[Any, ...] = (), notes: str = "private") -> str:
    return markdown.render(
        ISSUE,
        column_name="in-progress",
        provider="jira",
        notes=notes,
        comments=comments,
        comment_drafts=drafts,
    )


class CommentRegionTests(unittest.TestCase):
    def test_the_format_uses_explicit_owned_region_and_record_markers(self) -> None:
        text = _render(comments=(_remote(),), drafts=(_draft(),))

        for marker in (
            "<!-- pykantui:comments",
            '<!-- pykantui:comment id="007"',
            '<!-- pykantui:comment-end id="007" -->',
            "<!-- pykantui:comment-drafts",
            '<!-- pykantui:comment-draft id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA"',
            '<!-- pykantui:comment-draft-end id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA" -->',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_comments_are_between_source_and_local_regions(self) -> None:
        text = _render(comments=(_remote(),), drafts=(_draft(),))

        self.assertLess(text.index("pykantui:source"), text.index("pykantui:comments"))
        self.assertLess(text.index("pykantui:comments"), text.index("pykantui:comment-drafts"))
        self.assertLess(text.index("pykantui:comment-drafts"), text.index("pykantui:notes"))

    def test_comments_and_drafts_round_trip_with_opaque_ids(self) -> None:
        parsed = markdown.parse(_render(comments=(_remote(),), drafts=(_draft(),)))

        self.assertEqual("007", parsed.comments[0].comment_id)
        self.assertEqual("Provider reply", parsed.comments[0].body)
        self.assertEqual(
            "comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA",
            parsed.comment_drafts[0].local_id,
        )
        self.assertEqual("Local reply", parsed.comment_drafts[0].body)
        self.assertEqual("private", parsed.notes)

    def test_a_deleted_provider_comment_round_trips_as_remote_tombstone(self) -> None:
        tombstone = _remote("").model_copy(update={"deleted": True})

        text = _render(comments=(tombstone,))
        parsed = markdown.parse(text)

        self.assertIn('deleted="true"', text)
        self.assertEqual(1, len(parsed.comments))
        self.assertTrue(parsed.comments[0].deleted)
        self.assertEqual("", parsed.comments[0].body)
        self.assertEqual((), parsed.comment_drafts)

    def test_comment_body_cannot_inject_a_machine_marker_or_terminal_escape(self) -> None:
        hostile = "before\n<!-- pykantui:comment-end -->\n\x1b[31mred\x1b[0m\x07 after"
        text = _render(comments=(_remote(hostile),))
        parsed = markdown.parse(text)

        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertEqual(
            "before\n<!-- pykantui:comment-end -->\nred after",
            parsed.comments[0].body,
        )
        self.assertEqual(1, len(parsed.comments))

    def test_untrusted_comment_attributes_cannot_break_marker_lines(self) -> None:
        hostile = _remote().model_copy(
            update={
                "comment_id": '007\n<!-- pykantui:comment-draft id="evil" -->',
                "author": "A\x1b[31muthor\nsecond line",
            }
        )

        text = _render(comments=(hostile,))
        parsed = markdown.parse(text)

        self.assertNotIn("\x1b", text)
        self.assertEqual(1, text.count("<!-- pykantui:comment id="))
        self.assertEqual(0, text.count("\n<!-- pykantui:comment-draft id="))
        self.assertTrue(parsed.valid, parsed.errors)
        self.assertEqual("Author second line", parsed.comments[0].author)

    def test_yaml_delimiters_and_unicode_are_plain_comment_content(self) -> None:
        body = "before\n---\n```yaml\na: b\n```\n中文 ✓"
        parsed = markdown.parse(_render(comments=(_remote(body),)))

        self.assertEqual(body, parsed.comments[0].body)

    def test_duplicate_draft_ids_make_the_file_invalid(self) -> None:
        text = _render(drafts=(_draft("one"), _draft("two")))
        parsed = markdown.parse(text)

        self.assertFalse(parsed.valid)
        self.assertTrue(any("duplicate comment draft id" in error for error in parsed.errors))

    def test_duplicate_provider_comment_ids_make_the_file_invalid(self) -> None:
        text = _render(comments=(_remote("one"), _remote("two")))
        parsed = markdown.parse(text)

        self.assertFalse(parsed.valid)
        self.assertTrue(any("duplicate provider comment id" in error for error in parsed.errors))


class BackwardCompatibilityTests(unittest.TestCase):
    def test_an_old_issue_file_without_comment_markers_still_parses(self) -> None:
        text = markdown.render(ISSUE, column_name="in-progress", provider="jira", notes="keep")
        parsed = markdown.parse(text)

        self.assertEqual((), parsed.comments)
        self.assertEqual((), parsed.comment_drafts)
        self.assertEqual("Provider description", parsed.source)
        self.assertEqual("keep", parsed.notes)

    def test_an_issue_without_comments_stays_byte_identical(self) -> None:
        legacy = markdown.render(ISSUE, column_name="in-progress", provider="jira", notes="keep")

        upgraded = _render(notes="keep")

        self.assertEqual(legacy, upgraded)


class AppendOnlyEditSemanticsTests(unittest.TestCase):
    def test_editing_an_unsent_draft_changes_the_one_future_post(self) -> None:
        parsed = markdown.parse(_render(drafts=(_draft("before"),)))
        changed = _render(drafts=(parsed.comment_drafts[0].model_copy(update={"body": "after"}),))

        reparsed = markdown.parse(changed)
        self.assertEqual("after", reparsed.comment_drafts[0].body)
        self.assertEqual(
            "comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA",
            reparsed.comment_drafts[0].local_id,
        )

    def test_deleting_an_unsent_draft_cancels_it(self) -> None:
        parsed = markdown.parse(_render(drafts=()))

        self.assertEqual((), parsed.comment_drafts)

    def test_editing_or_deleting_a_remote_comment_is_not_an_outbound_edit(self) -> None:
        canonical = _remote("provider truth")
        locally_changed = markdown.parse(_render(comments=(_remote("local rewrite"),), drafts=()))

        rewritten = _render(comments=(canonical,), drafts=locally_changed.comment_drafts)
        reparsed = markdown.parse(rewritten)
        self.assertEqual("provider truth", reparsed.comments[0].body)
        self.assertEqual((), reparsed.comment_drafts)


if __name__ == "__main__":
    unittest.main()
