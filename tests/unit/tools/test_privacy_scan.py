"""Tests for the release privacy scanner."""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin
from tools.privacy_scan import PrivacyFinding, main, scan_repository


class PrivacyScanTests(unittest.TestCase):
    def test_denied_identity_is_reported_without_echoing_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            identity = "Private Person"
            (root / "README.md").write_text(f"Created by {identity}\n", encoding="utf-8")

            findings = scan_repository(root, denied=(identity,))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(root), "--deny", identity])

        self.assertEqual((PrivacyFinding(path="README.md", category="denied-identity"),), findings)
        self.assertEqual(1, status)
        self.assertEqual("README.md: denied-identity\n", output.getvalue())
        self.assertNotIn(identity, output.getvalue())

    def test_absolute_user_home_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            samples = {
                "windows.txt": "C:" + "\\Users\\private-user\\repo",
                "macos.txt": "/" + "Users/private-user/repo",
                "linux.txt": "/" + "home/private-user/repo",
            }
            for name, text in samples.items():
                (root / name).write_text(text, encoding="utf-8")

            self.assertEqual(
                (
                    PrivacyFinding(path="linux.txt", category="absolute-home-path"),
                    PrivacyFinding(path="macos.txt", category="absolute-home-path"),
                    PrivacyFinding(path="windows.txt", category="absolute-home-path"),
                ),
                scan_repository(root),
            )

    def test_git_ignored_private_files_are_not_publishable_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("private/\n", encoding="utf-8")
            private = root / "private"
            private.mkdir()
            (private / "capture.txt").write_text("Private Person", encoding="utf-8")
            (root / "safe.txt").write_text("synthetic fixture", encoding="utf-8")

            self.assertEqual((), scan_repository(root, denied=("Private Person",)))

    def test_public_container_home_and_provider_user_route_are_not_personal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "compose.txt").write_text("/" + "home/kbn/.pykantui", encoding="utf-8")
            (root / "route.txt").write_text("/users/me", encoding="utf-8")

            self.assertEqual((), scan_repository(root))

    def test_png_metadata_is_scanned_without_ocr_claims(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            identity = "Private Person"
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Author", identity)
            Image.new("RGB", (2, 2), "navy").save(root / "banner.png", pnginfo=metadata)

            self.assertEqual(
                (PrivacyFinding(path="banner.png", category="denied-image-metadata"),),
                scan_repository(root, denied=(identity,)),
            )

    def test_safe_synthetic_release_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "README.md").write_text("Maintained by pykantui contributors.\n", encoding="utf-8")
            Image.new("RGB", (2, 2), "navy").save(root / "banner.png")

            self.assertEqual((), scan_repository(root, denied=("Private Person",)))


if __name__ == "__main__":
    unittest.main()
