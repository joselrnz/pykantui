from __future__ import annotations

import unittest

from tools.live_noop_sync import terminal_is_safe


class LiveNoopSyncTests(unittest.TestCase):
    def test_terminal_text_rejects_failure_and_held_states(self) -> None:
        self.assertTrue(terminal_is_safe("Complete", "wrote 19"))
        self.assertFalse(terminal_is_safe("Complete · Held locally", "held 1"))
        self.assertFalse(terminal_is_safe("Failed", "network error"))
        self.assertFalse(terminal_is_safe("Complete", "skipped 1"))


if __name__ == "__main__":
    unittest.main()
