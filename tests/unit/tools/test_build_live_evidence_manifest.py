from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from tools.build_live_evidence_manifest import PROVIDERS, REQUIRED_CAPTURES, build_manifest

RUN_TAG = "PKT-E2E-20260814T122600Z-deadbeef"


class LiveEvidenceManifestTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        run = root / RUN_TAG
        run.mkdir(parents=True)
        receipts: list[dict[str, object]] = []
        sequence = 0
        for provider in PROVIDERS:
            for operation, operation_id in (
                ("tui-sync-create-batch", "create-19-v1"),
                ("tui-sync-edit-move-comment", "mutation-v1"),
                ("direct-remote-conflict-edit", "title-conflict-v1"),
            ):
                for event in ("attempted", "verified"):
                    sequence += 1
                    receipts.append(
                        {
                            "sequence": sequence,
                            "provider": provider,
                            "event": event,
                            "operation": operation,
                            "operation_id": operation_id,
                        }
                    )
            self._write_json(
                run / "live-sync" / provider / "create-api-readback.json",
                provider,
                created=19,
                direct_exact_reads=19,
                cards=[{"remote_id": str(index)} for index in range(19)],
            )
            self._write_json(
                run / "post-create" / provider / "actions.json",
                provider,
                provider_writes=0,
                comment_drafts=1,
            )
            self._write_json(
                run / "mutation-sync" / provider / "mutation-api-readback.json",
                provider,
                updates=2,
                moves=1,
                comments=1,
                direct_exact_reads=2,
                cards=[{"remote_id": "1"}, {"remote_id": "2"}],
                comment_ids=["comment-1"],
            )
            self._write_json(
                run / "conflict-sync" / provider / "conflict-api-readback.json",
                provider,
                aligned=True,
                resolution="provider",
                field="title",
            )
            self._write_json(
                run / "noop-sync" / provider / "noop-verification.json",
                provider,
                before_plan="empty",
                after_plan="empty",
                direct_remote_count=19,
                provider_mutations=0,
                tagged_markdown_bytes_stable=True,
                tagged_markdown_files=19,
                terminal_phase="Complete",
            )
            for relative in REQUIRED_CAPTURES:
                self._write_pair(run / relative.format(provider=provider))
        (run / "receipts.jsonl").write_text("".join(json.dumps(row) + "\n" for row in receipts), encoding="utf-8")
        return run

    @staticmethod
    def _write_json(path: Path, provider: str, **values: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"schema": 1, "run_tag": RUN_TAG, "provider": provider, **values}),
            encoding="utf-8",
        )

    @staticmethod
    def _write_pair(svg: Path) -> None:
        svg.parent.mkdir(parents=True, exist_ok=True)
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">'
            '<rect width="1" height="2" fill="#000"/><rect x="1" width="1" height="2" fill="#fff"/>'
            "</svg>",
            encoding="utf-8",
        )
        image = Image.new("RGB", (2, 2), "black")
        image.putpixel((1, 0), (255, 255, 255))
        image.save(svg.with_suffix(".png"))

    def test_builds_strict_all_provider_manifest_and_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._fixture(Path(raw))
            manifest = build_manifest(run)

            self.assertEqual(list(PROVIDERS), manifest["providers"])
            self.assertEqual(171, manifest["counts"]["remote_cards_created"])
            self.assertEqual(18, manifest["counts"]["updates"])
            self.assertEqual(9, manifest["counts"]["moves"])
            self.assertEqual(9, manifest["counts"]["comments"])
            self.assertEqual(9, manifest["counts"]["conflicts_resolved"])
            self.assertEqual(9, manifest["counts"]["no_op_syncs"])
            self.assertEqual(2 * len(PROVIDERS) * len(REQUIRED_CAPTURES), manifest["counts"]["artifacts"])
            self.assertTrue((run / "manifest.json").is_file())
            self.assertTrue((run / "summary.json").is_file())
            self.assertIn("Live provider certification", (run / "index.md").read_text(encoding="utf-8"))

    def test_fails_closed_on_missing_capture_or_bad_provider_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._fixture(Path(raw))
            missing = run / REQUIRED_CAPTURES[0].format(provider="jira")
            missing.unlink()
            with self.assertRaisesRegex(ValueError, "missing required screenshot"):
                build_manifest(run)

        with tempfile.TemporaryDirectory() as raw:
            run = self._fixture(Path(raw))
            readback = run / "live-sync" / "jira" / "create-api-readback.json"
            payload = json.loads(readback.read_text(encoding="utf-8"))
            payload["created"] = 18
            readback.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "created 18 cards"):
                build_manifest(run)

    def test_fails_closed_when_an_attempt_has_no_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._fixture(Path(raw))
            receipt_path = run / "receipts.jsonl"
            rows = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
            removed = False
            retained = []
            for row in rows:
                if row["provider"] == "jira" and row["event"] == "verified" and not removed:
                    removed = True
                    continue
                retained.append(row)
            rows = retained
            for sequence, row in enumerate(rows, start=1):
                row["sequence"] = sequence
            receipt_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unterminated receipt"):
                build_manifest(run)


if __name__ == "__main__":
    unittest.main()
