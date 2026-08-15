"""Run a discovered unittest shard in a deterministic random order."""

from __future__ import annotations

import argparse
import random
import unittest
from collections.abc import Collection, Iterator


def iter_cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    """Yield every test case from an arbitrarily nested suite."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def randomized_suite(
    pattern: str,
    seed: int,
    *,
    excluded_ids: Collection[str] = (),
) -> unittest.TestSuite:
    """Discover ``pattern`` and return non-deferred cases in seeded order."""
    discovered = unittest.defaultTestLoader.discover("tests", pattern=pattern, top_level_dir=".")
    excluded = frozenset(excluded_ids)
    cases = [case for case in iter_cases(discovered) if case.id() not in excluded]
    random.Random(seed).shuffle(cases)
    return unittest.TestSuite(cases)


def main() -> int:
    """Run one randomized shard and return a process status."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--exclude-id",
        action="append",
        default=[],
        help="defer one exact unittest id to another runner",
    )
    parser.add_argument("--verbosity", type=int, default=1, choices=(0, 1, 2))
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=args.verbosity).run(
        randomized_suite(args.pattern, args.seed, excluded_ids=args.exclude_id)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
