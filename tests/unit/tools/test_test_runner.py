"""Tests for deterministic randomized unittest execution."""

from __future__ import annotations

import unittest

from tools.test_runner import iter_cases, randomized_suite


class TestRunnerTests(unittest.TestCase):
    def test_iter_cases_flattens_nested_suites(self) -> None:
        first = unittest.FunctionTestCase(lambda: None, description="first")
        second = unittest.FunctionTestCase(lambda: None, description="second")
        nested = unittest.TestSuite((unittest.TestSuite((first,)), second))

        self.assertEqual([first, second], list(iter_cases(nested)))

    def test_seeded_order_is_reproducible(self) -> None:
        first = [case.id() for case in iter_cases(randomized_suite("test_api_client.py", 41))]
        second = [case.id() for case in iter_cases(randomized_suite("test_api_client.py", 41))]

        self.assertEqual(first, second)
        self.assertGreater(len(first), 0)
        self.assertEqual(len(first), len(set(first)), "discovery returned a test more than once")

    def test_exact_test_ids_can_be_deferred_to_a_serial_lane(self) -> None:
        discovered = [case.id() for case in iter_cases(randomized_suite("test_api_client.py", 41))]
        deferred = discovered[0]

        filtered = [
            case.id()
            for case in iter_cases(
                randomized_suite("test_api_client.py", 41, excluded_ids=(deferred,))
            )
        ]

        self.assertNotIn(deferred, filtered)
        self.assertEqual(set(discovered) - {deferred}, set(filtered))


if __name__ == "__main__":
    unittest.main()
