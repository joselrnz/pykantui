from __future__ import annotations

import unittest

from tools.live_tui_mutation_sync import post_sync_kind, remote_stub


class LiveMutationSyncTests(unittest.TestCase):
    def test_classifies_only_the_two_tagged_post_sync_titles(self) -> None:
        tag = "PKT-E2E-TAG"
        self.assertEqual(post_sync_kind(f"[{tag}:jira] card 01 · TUI · PostSync", tag, "jira"), "tui")
        self.assertEqual(
            post_sync_kind(f"[{tag}:jira] card 02 · Markdown · PostSyncMD", tag, "jira"),
            "markdown",
        )
        self.assertIsNone(post_sync_kind(f"[{tag}:jira] card 03", tag, "jira"))
        self.assertIsNone(post_sync_kind(f"[{tag}:github] card 01 · TUI · PostSync", tag, "jira"))

    def test_github_exact_read_stub_preserves_issue_number(self) -> None:
        stub = remote_stub("github", "5152021932", "repo#4", "title")
        self.assertEqual(stub.issue_id, "5152021932")
        self.assertEqual(stub.extra, {"number": 4})


if __name__ == "__main__":
    unittest.main()
