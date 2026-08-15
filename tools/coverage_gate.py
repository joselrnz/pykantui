"""Run concurrent coverage shards, isolated latency checks, and the threshold."""

from __future__ import annotations

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

SHARDS = (
    "test_[a-c]*.py",
    "test_[d-g]*.py",
    "test_i18n.py",
    "test_i18n_cli.py",
    "test_i18n_config.py",
    "test_i18n_tui.py",
    "test_init_cli.py",
    "test_init_wizard.py",
    "test_[j-l]*.py",
    "test_m[a-l]*.py",
    "test_m[m-z]*.py",
    "test_[o-p]*.py",
    "test_[q-s]*.py",
    "test_t[a-m]*.py",
    "test_t[n-z]*.py",
    "test_[u-z]*.py",
)
BASE_SEED = 20260811
# Wall-time budgets and intentionally transient UI states become host-contention
# measurements when every CPU is occupied by another coverage process. These
# tests are omitted from the concurrent filename shards and run exactly once,
# under coverage, after the pool drains.
SERIAL_COVERAGE_TEST_IDS = (
    "tests.edge_cases.providers.test_provider_render_load."
    "LargeTextualRendererTests.test_one_thousand_card_kanban_respects_the_regression_budget",
    "tests.integration.tui.test_board_tui."
    "JumpModeTests.test_l_only_targets_and_enter_commits",
    "tests.integration.tui.test_board_tui."
    "RefreshFailureTests.test_a_broken_reload_notifies_instead_of_crashing",
)
_UNITTEST_SUMMARY = re.compile(
    r"^Ran (?P<count>\d+) tests? in (?P<seconds>\d+(?:\.\d+)?)s$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class ShardResult:
    """One isolated shard result and its captured diagnostic output."""

    pattern: str
    returncode: int
    output: str


def _shard_command(index: int, pattern: str) -> list[str]:
    """Build one parallel coverage-shard command."""
    command = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "--parallel-mode",
        "tools/test_runner.py",
        "--pattern",
        pattern,
        "--seed",
        str(BASE_SEED + index),
    ]
    for test_id in SERIAL_COVERAGE_TEST_IDS:
        command.extend(("--exclude-id", test_id))
    return command


def _serial_test_command(test_id: str) -> list[str]:
    """Build an isolated coverage command for one latency-sensitive test."""
    return [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "--parallel-mode",
        "-m",
        "unittest",
        test_id,
    ]


def _run_shard(index: int, pattern: str) -> ShardResult:
    command = _shard_command(index, pattern)
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return ShardResult(pattern=pattern, returncode=completed.returncode, output=completed.stdout)


def _run_serial_test(test_id: str) -> ShardResult:
    completed = subprocess.run(
        _serial_test_command(test_id),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return ShardResult(pattern=test_id, returncode=completed.returncode, output=completed.stdout)


def _parse_unittest_summary(output: str) -> tuple[int, float] | None:
    """Return the final unittest count and duration from captured output."""
    matches = tuple(_UNITTEST_SUMMARY.finditer(output))
    if not matches:
        return None
    match = matches[-1]
    return int(match.group("count")), float(match.group("seconds"))


def _report_results(results: tuple[ShardResult, ...], *, phase: str) -> bool:
    """Print structured diagnostics and verify every child ran real tests."""
    successful = True
    reported_tests = 0
    aggregate_seconds = 0.0
    for result in results:
        summary = _parse_unittest_summary(result.output)
        if summary is None:
            verdict = "failed (missing unittest summary)"
            successful = False
        else:
            test_count, seconds = summary
            reported_tests += test_count
            aggregate_seconds += seconds
            label = "test" if test_count == 1 else "tests"
            verdict = f"ok ({test_count} {label}, {seconds:.3f}s)"
            if result.returncode != 0 or test_count == 0:
                verdict = f"failed ({test_count} {label}, {seconds:.3f}s)"
                successful = False
        print(f"{result.pattern}: {verdict}")
        if result.returncode or summary is None:
            print(result.output[-4000:])
        if result.returncode:
            successful = False
    total_label = "test" if reported_tests == 1 else "tests"
    print(
        f"{phase} total: {reported_tests} {total_label} "
        f"({aggregate_seconds:.3f}s aggregate child time)"
    )
    return successful


def main() -> int:
    """Run all shards, combine their data, and apply the configured floor."""
    for data_file in Path.cwd().glob(".coverage*"):
        if data_file.is_file():
            data_file.unlink()

    with ThreadPoolExecutor(max_workers=len(SHARDS)) as executor:
        results = tuple(executor.map(lambda item: _run_shard(*item), enumerate(SHARDS)))
    if not _report_results(results, phase="parallel"):
        return 1

    serial_results = tuple(_run_serial_test(test_id) for test_id in SERIAL_COVERAGE_TEST_IDS)
    if not _report_results(serial_results, phase="serial"):
        return 1

    subprocess.run([sys.executable, "-m", "coverage", "combine"], check=True)
    return subprocess.run([sys.executable, "-m", "coverage", "report"], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
