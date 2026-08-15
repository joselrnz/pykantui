"""Tests for contention-safe coverage scheduling."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from tools.coverage_gate import (
    SERIAL_COVERAGE_TEST_IDS,
    SHARDS,
    ShardResult,
    _parse_unittest_summary,
    _report_results,
    _serial_test_command,
    _shard_command,
    main,
)
from tools.test_runner import iter_cases, randomized_suite


class CoverageGateTests(unittest.TestCase):
    EXPECTED_SERIAL_TEST_IDS = (
        "tests.edge_cases.providers.test_provider_render_load."
        "LargeTextualRendererTests.test_one_thousand_card_kanban_respects_the_regression_budget",
        "tests.integration.tui.test_board_tui."
        "JumpModeTests.test_l_only_targets_and_enter_commits",
        "tests.integration.tui.test_board_tui."
        "RefreshFailureTests.test_a_broken_reload_notifies_instead_of_crashing",
    )

    def test_unittest_summary_parser_handles_singular_and_plural_counts(self) -> None:
        singular = _parse_unittest_summary(".\nRan 1 test in 0.125s\n\nOK\n")
        plural = _parse_unittest_summary("...\nRan 37 tests in 2.500s\n\nOK\n")

        self.assertEqual((1, 0.125), singular)
        self.assertEqual((37, 2.5), plural)

    def test_success_report_includes_counts_without_dumping_child_output(self) -> None:
        result = ShardResult(
            pattern="test_[a-f]*.py",
            returncode=0,
            output="sensitive-success-log\nRan 12 tests in 3.250s\n\nOK\n",
        )
        stream = io.StringIO()

        with contextlib.redirect_stdout(stream):
            successful = _report_results((result,), phase="parallel")

        report = stream.getvalue()
        self.assertTrue(successful)
        self.assertIn("test_[a-f]*.py: ok (12 tests, 3.250s)", report)
        self.assertIn("parallel total: 12 tests", report)
        self.assertNotIn("sensitive-success-log", report)

    def test_zero_exit_without_a_test_summary_is_an_incomplete_failure(self) -> None:
        result = ShardResult(pattern="test_[a-f]*.py", returncode=0, output="unexpected output")
        stream = io.StringIO()

        with contextlib.redirect_stdout(stream):
            successful = _report_results((result,), phase="parallel")

        self.assertFalse(successful)
        self.assertIn("failed (missing unittest summary)", stream.getvalue())

    def test_serial_lane_declares_the_three_contention_sensitive_tests(self) -> None:
        self.assertEqual(self.EXPECTED_SERIAL_TEST_IDS, SERIAL_COVERAGE_TEST_IDS)

    def test_parallel_shards_explicitly_defer_all_serial_tests(self) -> None:
        command = _shard_command(0, SHARDS[0])

        deferred = [
            command[index + 1]
            for index, argument in enumerate(command)
            if argument == "--exclude-id"
        ]
        self.assertEqual(list(SERIAL_COVERAGE_TEST_IDS), deferred)

    def test_parallel_manifest_is_complete_unique_and_bounded(self) -> None:
        all_ids = {
            case.id()
            for case in iter_cases(randomized_suite("test_*.py", 1))
        }
        occurrences: dict[str, int] = {}
        shard_sizes: list[int] = []

        for index, pattern in enumerate(SHARDS):
            ids = [
                case.id()
                for case in iter_cases(
                    randomized_suite(
                        pattern,
                        1 + index,
                        excluded_ids=SERIAL_COVERAGE_TEST_IDS,
                    )
                )
            ]
            shard_sizes.append(len(ids))
            for test_id in ids:
                occurrences[test_id] = occurrences.get(test_id, 0) + 1

        parallel_ids = all_ids - set(SERIAL_COVERAGE_TEST_IDS)
        self.assertEqual(parallel_ids, set(occurrences))
        self.assertTrue(all(count == 1 for count in occurrences.values()))
        self.assertLessEqual(max(shard_sizes), 350, shard_sizes)

    def test_serial_lane_runs_each_exact_test_under_parallel_coverage_data_mode(self) -> None:
        for test_id in SERIAL_COVERAGE_TEST_IDS:
            with self.subTest(test_id=test_id):
                command = _serial_test_command(test_id)

                self.assertEqual(
                    [
                        command[0],
                        "-m",
                        "coverage",
                        "run",
                        "--parallel-mode",
                        "-m",
                        "unittest",
                        test_id,
                    ],
                    command,
                )

    def test_main_runs_serial_lane_after_all_parallel_shards(self) -> None:
        events: list[str] = []

        def run_shard(index: int, pattern: str) -> ShardResult:
            events.append(f"shard:{index}:{pattern}")
            return ShardResult(pattern=pattern, returncode=0, output="Ran 1 test in 0.001s\n\nOK\n")

        def run_serial(test_id: str) -> ShardResult:
            if not any(event.startswith("serial:") for event in events):
                self.assertEqual(len(SHARDS), len(events))
            events.append(f"serial:{test_id}")
            return ShardResult(pattern=test_id, returncode=0, output="Ran 1 test in 0.001s\n\nOK\n")

        def run_command(*args: object, **kwargs: object) -> SimpleNamespace:
            del kwargs
            command = cast(list[str], args[0])
            events.append(f"coverage:{command[-1]}")
            return SimpleNamespace(returncode=0)

        stream = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("tools.coverage_gate.Path.cwd", return_value=Path(directory)),
            patch("tools.coverage_gate._run_shard", side_effect=run_shard),
            patch("tools.coverage_gate._run_serial_test", side_effect=run_serial),
            patch("tools.coverage_gate.subprocess.run", side_effect=run_command),
            contextlib.redirect_stdout(stream),
        ):
            self.assertEqual(0, main())

        serial_events = [event for event in events if event.startswith("serial:")]
        self.assertEqual([f"serial:{test_id}" for test_id in SERIAL_COVERAGE_TEST_IDS], serial_events)
        first_serial = events.index(serial_events[0])
        self.assertTrue(all(event.startswith("shard:") for event in events[:first_serial]))
        self.assertEqual("coverage:combine", events[-2])
        self.assertEqual("coverage:report", events[-1])
        self.assertIn("serial total: 3 tests", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
