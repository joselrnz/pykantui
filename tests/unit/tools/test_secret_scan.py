"""Tests for the repository secret-scanning release gate."""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.secret_scan import Finding, candidate_files, main, scan_repository


def _github_token(character: str = "A") -> str:
    """Build a realistic token without putting one in this repository."""

    return "ghp_" + character * 36


class SecretScanTests(unittest.TestCase):
    def test_git_discovers_tracked_and_untracked_nonignored_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("ignored.txt\n.env\n", encoding="utf-8")
            (root / "tracked.txt").write_text("safe", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", ".gitignore", "tracked.txt"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            (root / "untracked.txt").write_text("safe", encoding="utf-8")
            (root / "ignored.txt").write_text(_github_token(), encoding="utf-8")
            (root / ".env").write_text(f"GITHUB_TOKEN={_github_token()}\n", encoding="utf-8")

            discovered = tuple(path.relative_to(root).as_posix() for path in candidate_files(root))

            self.assertEqual((".gitignore", "tracked.txt", "untracked.txt"), discovered)
            self.assertEqual((), scan_repository(root))

    def test_filesystem_fallback_skips_binary_generated_and_private_directories(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            token = _github_token()
            (root / "visible.txt").write_text(token, encoding="utf-8")
            (root / "binary.dat").write_bytes(b"prefix\x00" + token.encode())
            for directory in (".git", ".venv", "artifacts", "build", "dist"):
                path = root / directory
                path.mkdir()
                (path / "leak.txt").write_text(token, encoding="utf-8")

            self.assertEqual(
                (Finding(path="visible.txt", category="github-token"),),
                scan_repository(root),
            )

    def test_common_token_families_and_private_keys_are_categorized(self) -> None:
        samples = {
            "github.txt": _github_token("G"),
            "gitlab.txt": "glpat-" + "L" * 24,
            "aws.txt": "AKIA" + "1A" * 8,
            "slack.txt": "xoxb-123456789012-123456789012-" + "s" * 24,
            "stripe.txt": "sk_live_" + "S" * 24,
            "openai.txt": "sk-proj-" + "O" * 40,
            "google.txt": "AIza" + "G" * 35,
            "linear.txt": "lin_api_" + "N" * 40,
            "clickup.txt": "pk_12345678_" + "C" * 32,
            "plane.txt": "plane_api_" + "P" * 32,
            "atlassian.txt": "ATATT" + "T" * 40,
            "trello.txt": "ATTA" + "R" * 48,
            "jwt.txt": "eyJ" + "a" * 20 + "." + "b" * 24 + "." + "c" * 24,
            "private.pem": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nnot-a-real-key\n",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, value in samples.items():
                (root / name).write_text(value, encoding="utf-8")

            findings = scan_repository(root)

        self.assertEqual(
            {
                "atlassian-token",
                "aws-access-key",
                "clickup-token",
                "github-token",
                "gitlab-token",
                "google-api-key",
                "jwt",
                "linear-token",
                "openai-key",
                "plane-token",
                "private-key",
                "slack-token",
                "stripe-live-key",
                "trello-token",
            },
            {finding.category for finding in findings},
        )

    def test_local_env_values_are_matched_exactly_without_being_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text(".env\n", encoding="utf-8")
            secret = "opaque-value-without-a-known-prefix-938475"
            (root / ".env").write_text(
                "PROJECT_ID=123\n"
                "EMPTY=\n"
                "PASSWORD=changeme\n"
                f"CUSTOM_SECRET='{secret}' # local only\n",
                encoding="utf-8",
            )
            (root / "copied.txt").write_text(f"accidentally copied: {secret}", encoding="utf-8")

            findings = scan_repository(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(root)])

            self.assertEqual((Finding(path="copied.txt", category="local-env-secret"),), findings)
            self.assertEqual(1, status)
            self.assertEqual("copied.txt: local-env-secret\n", output.getvalue())
            self.assertNotIn(secret, output.getvalue())

    def test_documented_placeholders_and_redaction_regex_literals_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sample = root / ".env.example"
            sample.write_text(
                "GITHUB_TOKEN=\n"
                "CLICKUP_TOKEN=pk_12345678_ABCDEF...\n"
                "JIRA_TOKEN=ATATT3xFfGF0...\n"
                "PASSWORD=changeme\n",
                encoding="utf-8",
            )
            redaction = root / "src" / "pykantui" / "api" / "redaction.py"
            redaction.parent.mkdir(parents=True)
            redaction.write_text(
                're.compile(r"\\bgithub_pat_[A-Za-z0-9_]{8,}")\n'
                're.compile(r"\\bglpat-[A-Za-z0-9._-]{8,}")\n',
                encoding="utf-8",
            )

            self.assertEqual((), scan_repository(root))

            (root / "elsewhere.py").write_text(_github_token(), encoding="utf-8")
            self.assertEqual(
                (Finding(path="elsewhere.py", category="github-token"),),
                scan_repository(root),
            )

    def test_findings_are_deduplicated_and_sorted_by_path_then_category(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            token = _github_token()
            (root / "z.txt").write_text(f"{token}\n{token}\n", encoding="utf-8")
            (root / "a.pem").write_text(
                f"{token}\n-----BEGIN " + "PRIVATE KEY-----\nignored body\n",
                encoding="utf-8",
            )

            self.assertEqual(
                (
                    Finding(path="a.pem", category="github-token"),
                    Finding(path="a.pem", category="private-key"),
                    Finding(path="z.txt", category="github-token"),
                ),
                scan_repository(root),
            )

    def test_empty_git_repository_does_not_fall_back_to_ignored_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".git" / "info" / "exclude").write_text("ignored.txt\n", encoding="utf-8")
            (root / "ignored.txt").write_text(_github_token(), encoding="utf-8")

            self.assertEqual((), candidate_files(root))
            self.assertEqual((), scan_repository(root))

    def test_tracked_env_file_is_scanned_instead_of_treated_as_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            secret = "opaque-tracked-credential-value-485759"
            (root / ".env").write_text(f"CUSTOM_SECRET={secret}\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".env"], check=True, stdout=subprocess.DEVNULL)

            self.assertEqual((root / ".env",), candidate_files(root))
            self.assertEqual(
                (Finding(path=".env", category="local-env-secret"),),
                scan_repository(root),
            )

    def test_a_secret_in_a_filename_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secret = "opaque-filename-credential-394857"
            (root / ".env").write_text(f"CUSTOM_TOKEN={secret}\n", encoding="utf-8")
            unsafe = root / f"copied-{secret}-name.txt"
            unsafe.write_text(secret, encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(root)])

            self.assertEqual(1, status)
            self.assertNotIn(secret, output.getvalue())
            self.assertEqual(
                ".env: local-env-secret\ncopied-[REDACTED]-name.txt: local-env-secret\n",
                output.getvalue(),
            )


if __name__ == "__main__":
    unittest.main()
