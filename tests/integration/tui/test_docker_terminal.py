"""The interactive development shell must preserve the theme's true colors."""

from __future__ import annotations

import os
import unittest
from pathlib import Path


class DockerTerminalTests(unittest.TestCase):
    def test_image_does_not_pin_terminal_dimensions(self) -> None:
        self.assertIsNone(os.environ.get("COLUMNS"))
        self.assertIsNone(os.environ.get("LINES"))

    def test_interactive_shell_advertises_truecolor(self) -> None:
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        shell = compose.split("\n  shell:\n", 1)[1]

        self.assertIn("COLORTERM: truecolor", shell)
        self.assertIn("TERM: xterm-256color", shell)


if __name__ == "__main__":
    unittest.main()
