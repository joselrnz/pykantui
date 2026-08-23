"""Distribution-safe media links in the project README."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]
README = ROOT / "README.md"
RAW_ASSET_ROOT = "https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/"


class ReadmeMediaTests(unittest.TestCase):
    def test_pypi_visible_media_uses_absolute_public_urls(self) -> None:
        source = README.read_text(encoding="utf-8")
        urls = re.findall(r'<img\s+src="([^"]+)"', source)

        self.assertTrue(urls, "README should contain its banner and demonstrations")
        self.assertTrue(all(url.startswith(RAW_ASSET_ROOT) for url in urls))

    def test_every_readme_media_url_has_a_repository_asset(self) -> None:
        source = README.read_text(encoding="utf-8")
        urls = re.findall(r'<img\s+src="([^"]+)"', source)

        missing = [url for url in urls if not (ROOT / "assets" / url.removeprefix(RAW_ASSET_ROOT)).is_file()]

        self.assertEqual([], missing)

    def test_readme_uses_the_interactive_banner(self) -> None:
        source = README.read_text(encoding="utf-8")

        self.assertIn(f'{RAW_ASSET_ROOT}pykantui-banner-v3.gif', source)


if __name__ == "__main__":
    unittest.main()
