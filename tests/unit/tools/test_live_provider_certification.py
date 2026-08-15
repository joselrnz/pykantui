"""Safety contract for the opt-in live provider certification harness."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tools.live_provider_certification import (
    AmbiguousWriteError,
    CertificationContext,
    CertificationRunner,
    MutationRecord,
    OwnershipError,
    ReadbackRequest,
    ReceiptLog,
    ReplayBlockedError,
    WritesDisabledError,
    generate_run_tag,
    owned_title,
)


class LiveCertificationSafetyTests(unittest.TestCase):
    def context(self) -> CertificationContext:
        return CertificationContext(
            provider="jira",
            expected_project_id="JPT",
            actual_project_id="JPT",
            run_tag="PKT-E2E-20260814T120000Z-deadbeef",
        )

    def test_run_tags_and_owned_titles_are_exact_and_deterministic(self) -> None:
        tag = generate_run_tag(
            now=datetime(2026, 8, 14, 12, 30, tzinfo=UTC),
            nonce="01234567",
        )

        self.assertEqual("PKT-E2E-20260814T123000Z-01234567", tag)
        self.assertEqual(f"[{tag}:jira] create", owned_title(tag, "Jira"))
        self.assertEqual(f"[{tag}:jira] edited", owned_title(tag, "Jira", "edited"))

    def test_context_rejects_a_project_mismatch_before_any_write(self) -> None:
        with self.assertRaises(OwnershipError):
            CertificationContext(
                provider="jira",
                expected_project_id="JPT",
                actual_project_id="OTHER",
                run_tag="PKT-E2E-20260814T120000Z-deadbeef",
            )

    def test_context_rejects_non_owned_titles(self) -> None:
        context = self.context()

        context.assert_owned(project_id="JPT", title=owned_title(context.run_tag, "jira"))
        for title in ("ordinary card", f"[{context.run_tag}:jira] edited", f"[{context.run_tag}:github] create"):
            with self.subTest(title=title), self.assertRaises(OwnershipError):
                context.assert_owned(project_id="JPT", title=title)

    def test_dry_run_is_default_and_never_calls_the_sender(self) -> None:
        called = False

        def send() -> MutationRecord:
            nonlocal called
            called = True
            raise AssertionError("dry-run called the provider")

        with tempfile.TemporaryDirectory() as raw:
            runner = CertificationRunner(self.context(), ReceiptLog(Path(raw) / "receipts.jsonl"))
            result = runner.run_single_shot("create", "card-1", send, lambda request: None)

        self.assertTrue(result.dry_run)
        self.assertFalse(called)

    def test_execute_needs_both_the_flag_and_exact_environment_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            log = ReceiptLog(Path(raw) / "receipts.jsonl")
            for execute, environment in (
                (False, {"PYKANTUI_LIVE_WRITES": "1"}),
                (True, {}),
                (True, {"PYKANTUI_LIVE_WRITES": "true"}),
            ):
                runner = CertificationRunner(
                    self.context(), log, execute=execute, environment=environment
                )
                with self.subTest(execute=execute, environment=environment), self.assertRaises(
                    WritesDisabledError
                ):
                    runner.require_writes_enabled()

    def test_success_requires_direct_uncached_exact_readback(self) -> None:
        context = self.context()
        title = owned_title(context.run_tag, context.provider)
        requests: list[ReadbackRequest] = []

        def send() -> MutationRecord:
            return MutationRecord(remote_id="JPT-99", project_id="JPT", title=title)

        def readback(request: ReadbackRequest) -> MutationRecord:
            requests.append(request)
            return MutationRecord(remote_id="JPT-99", project_id="JPT", title=title)

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "receipts.jsonl"
            runner = CertificationRunner(
                context,
                ReceiptLog(path),
                execute=True,
                environment={"PYKANTUI_LIVE_WRITES": "1"},
            )
            result = runner.run_single_shot("create", "card-1", send, readback)

            events = [json.loads(line)["event"] for line in path.read_text().splitlines()]

        self.assertFalse(result.dry_run)
        self.assertEqual(["attempted", "accepted", "verified"], events)
        self.assertEqual(1, len(requests))
        self.assertTrue(requests[0].bypass_cache)
        self.assertEqual("JPT-99", requests[0].remote_id)

    def test_wrong_readback_identity_fails_closed(self) -> None:
        context = self.context()
        title = owned_title(context.run_tag, context.provider)
        with tempfile.TemporaryDirectory() as raw:
            runner = CertificationRunner(
                context,
                ReceiptLog(Path(raw) / "receipts.jsonl"),
                execute=True,
                environment={"PYKANTUI_LIVE_WRITES": "1"},
            )
            with self.assertRaises(OwnershipError):
                runner.run_single_shot(
                    "create",
                    "card-1",
                    lambda: MutationRecord("JPT-99", "JPT", title),
                    lambda request: MutationRecord(request.remote_id, "OTHER", title),
                )

    def test_ambiguous_post_is_receipted_once_and_never_replayed(self) -> None:
        attempts = 0

        def ambiguous() -> MutationRecord:
            nonlocal attempts
            attempts += 1
            raise TimeoutError("response was lost")

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "receipts.jsonl"
            runner = CertificationRunner(
                self.context(),
                ReceiptLog(path),
                execute=True,
                environment={"PYKANTUI_LIVE_WRITES": "1"},
            )
            with self.assertRaises(AmbiguousWriteError):
                runner.run_single_shot("comment", "comment-local-1", ambiguous, lambda request: None)
            with self.assertRaises(ReplayBlockedError):
                runner.run_single_shot("comment", "comment-local-1", ambiguous, lambda request: None)

            records = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual(1, attempts)
        self.assertEqual(["attempted", "ambiguous"], [record["event"] for record in records])
        self.assertNotIn("response was lost", json.dumps(records))

    def test_receipts_append_valid_redacted_path_safe_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "receipts.jsonl"
            log = ReceiptLog(path, sensitive_values=("opaque-live-value",))
            log.append(
                event="attempted",
                context=self.context(),
                operation="create",
                operation_id="card-1",
                details={
                    "token": "must-not-survive",
                    "note": "contains opaque-live-value",
                    "workspace": str(Path(raw).resolve()),
                    "safe": "relative/nonsecret",
                },
            )
            log.append(
                event="verified",
                context=self.context(),
                operation="create",
                operation_id="card-1",
            )

            lines = path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]

        serialized = json.dumps(records)
        self.assertEqual(2, len(records))
        self.assertEqual([1, 2], [record["sequence"] for record in records])
        self.assertNotIn("token", records[0].get("details", {}))
        self.assertNotIn("must-not-survive", serialized)
        self.assertNotIn("opaque-live-value", serialized)
        self.assertNotIn(str(Path(raw).resolve()), serialized)
        self.assertEqual("relative/nonsecret", records[0]["details"]["safe"])

    def test_attempt_receipt_blocks_replay_in_a_new_process_runner(self) -> None:
        context = self.context()
        title = context.title
        with tempfile.TemporaryDirectory() as raw:
            log = ReceiptLog(Path(raw) / "receipts.jsonl")
            first = CertificationRunner(
                context,
                log,
                execute=True,
                environment={"PYKANTUI_LIVE_WRITES": "1"},
            )
            first.run_single_shot(
                "create",
                "card-1",
                lambda: MutationRecord("JPT-99", "JPT", title),
                lambda request: MutationRecord(request.remote_id, "JPT", title),
            )
            restarted = CertificationRunner(
                context,
                log,
                execute=True,
                environment={"PYKANTUI_LIVE_WRITES": "1"},
            )

            with self.assertRaises(ReplayBlockedError):
                restarted.run_single_shot(
                    "create",
                    "card-1",
                    lambda: MutationRecord("JPT-100", "JPT", title),
                    lambda request: MutationRecord(request.remote_id, "JPT", title),
                )

    def test_operation_can_pin_a_later_exact_owned_title(self) -> None:
        context = self.context()
        edited = owned_title(context.run_tag, context.provider, "edited")
        with tempfile.TemporaryDirectory() as raw:
            runner = CertificationRunner(
                context,
                ReceiptLog(Path(raw) / "receipts.jsonl"),
                execute=True,
                environment={"PYKANTUI_LIVE_WRITES": "1"},
            )

            result = runner.run_single_shot(
                "edit",
                "card-1-edit",
                lambda: MutationRecord("JPT-99", "JPT", edited),
                lambda request: MutationRecord(request.remote_id, "JPT", edited),
                expected_title=edited,
            )

        self.assertEqual(edited, result.record.title if result.record else "")


if __name__ == "__main__":
    unittest.main()
