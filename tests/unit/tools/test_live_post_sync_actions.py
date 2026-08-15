from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.live_post_sync_actions import raw_post_sync_markdown_edit, select_next_column


class LivePostSyncActionTests(unittest.TestCase):
    def test_raw_markdown_edit_changes_only_owned_title_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "JPT-100.md"
            path.write_text(
                "---\nid: JPT-100\ntitle: '[TAG:jira] card 02 · Markdown'\nstatus: To Do\n---\n\n"
                "Original body.\n\n<!-- pykantui:notes -->\nPrivate note.\n",
                encoding="utf-8",
            )

            raw_post_sync_markdown_edit(path, run_tag="TAG", provider="jira")

            result = path.read_text(encoding="utf-8")
            self.assertIn("title: '[TAG:jira] card 02 · Markdown · PostSyncMD'", result)
            self.assertIn("Original body.\n\nMarkdown post-sync edit for TAG.", result)
            self.assertIn("<!-- pykantui:notes -->\nPrivate note.", result)

    def test_raw_markdown_edit_refuses_unowned_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "JPT-1.md"
            path.write_text("---\nid: JPT-1\ntitle: Other\n---\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unowned"):
                raw_post_sync_markdown_edit(path, run_tag="TAG", provider="jira")

    def test_provider_without_body_editability_keeps_source_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monday.md"
            path.write_text(
                "---\nid: '1'\ntitle: '[TAG:monday] card 02 · Markdown'\n---\n\nOriginal.\n",
                encoding="utf-8",
            )
            raw_post_sync_markdown_edit(
                path,
                run_tag="TAG",
                provider="monday",
                edit_body=False,
            )
            result = path.read_text(encoding="utf-8")
            self.assertIn("· Markdown · PostSyncMD'", result)
            self.assertIn("\nOriginal.\n", result)
            self.assertNotIn("Markdown post-sync edit for TAG", result)

    def test_next_column_wraps_and_never_returns_current_when_possible(self) -> None:
        self.assertEqual(select_next_column(("todo", "doing", "done"), "todo"), "doing")
        self.assertEqual(select_next_column(("todo", "doing", "done"), "done"), "todo")
        self.assertEqual(select_next_column(("only",), "only"), "only")


if __name__ == "__main__":
    unittest.main()
