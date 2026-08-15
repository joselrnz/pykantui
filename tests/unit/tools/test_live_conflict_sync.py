from __future__ import annotations

import unittest

from tools.live_conflict_sync import conflict_titles


class LiveConflictSyncTests(unittest.TestCase):
    def test_conflict_titles_are_distinct_owned_and_idempotent(self) -> None:
        original = "[TAG:jira] card 03"
        remote, local = conflict_titles(original, run_tag="TAG", provider="jira")
        self.assertEqual(remote, original + " · RemoteConflict")
        self.assertEqual(local, original + " · LocalConflict")
        self.assertNotEqual(remote, local)
        self.assertEqual(conflict_titles(remote, run_tag="TAG", provider="jira")[0], remote)

    def test_conflict_titles_refuse_unowned_input(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unowned"):
            conflict_titles("someone else's card", run_tag="TAG", provider="jira")


if __name__ == "__main__":
    unittest.main()
