"""Tamper-evident plan artifact persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pykantui.batch.planner import BatchPlan, load_batch_plan, write_batch_plan
from pykantui.tracker.errors import ProviderError


def plan() -> BatchPlan:
    now = datetime.now(UTC)
    return BatchPlan(
        batch_id="example",
        provider="jira",
        project_id="100",
        source_path="issues.yml",
        source_hash="abc",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        operations=(),
    ).with_hash()


class PlanFileTests(unittest.TestCase):
    def test_plan_round_trip_keeps_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "issues.plan.json"
            write_batch_plan(path, plan())
            loaded = load_batch_plan(path)

        self.assertTrue(loaded.verify_hash())

    def test_tampered_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "issues.plan.json"
            write_batch_plan(path, plan())
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["project_id"] = "evil"
            path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ProviderError, "tampered"):
                load_batch_plan(path)


if __name__ == "__main__":
    unittest.main()
