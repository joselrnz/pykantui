"""Release artifact compatibility contracts."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]


class ReleasePackagingTests(unittest.TestCase):
    def test_hatchling_remains_compatible_with_the_twine_release_gate(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        expected = "hatchling>=1.27,<1.30"
        self.assertIn(expected, project["build-system"]["requires"])
        self.assertIn(expected, project["project"]["optional-dependencies"]["dev"])


if __name__ == "__main__":
    unittest.main()
