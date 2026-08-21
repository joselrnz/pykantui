"""Linear, and the identifier a person can actually find.

Linear's API identifies a team by UUID. Its web UI shows you the team **key**
("ENG") and the team **name** ("Alex") and never the UUID at all — so asking
for one is asking for something the product does not offer. Reported from a
real setup attempt: the UUID could not be found in the interface, so the team
name was entered instead, and nothing worked.

These tests are mostly about that: whatever a person could plausibly have to
hand should resolve, and a wrong value should say what the real options are
rather than failing somewhere deeper.
"""

from __future__ import annotations

import unittest
from typing import Any

from pykantui.providers.linear import LinearProvider, _is_uuid
from pykantui.providers.linear.mapper import issue_to_remote, team_to_remote
from pykantui.providers.linear.schemas import IssueWire, TeamWire
from pykantui.tracker.errors import ProviderError

TEAM_UUID = "aaaa1111-bbbb-2222-cccc-333344445555"

TEAMS = {
    "teams": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [
            {"id": TEAM_UUID, "key": "OPS", "name": "Platform", "description": ""},
            {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "key": "ENG", "name": "Engineering"},
        ],
    }
}

STATES = {
    "team": {
        "states": {
            "nodes": [
                {"id": "s1", "name": "Todo", "type": "unstarted", "position": 1},
                {"id": "s2", "name": "In Progress", "type": "started", "position": 2},
            ]
        }
    }
}


class FakeHttp:
    """Answers the two queries this provider makes, and counts them."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        self.calls.append(query)
        if "teams (" in query:
            return TEAMS
        if "states" in query:
            # Only ever asked with a resolved UUID; assert it here so a
            # regression cannot pass by resolving to the wrong thing.
            assert (variables or {}).get("team", "").count("-") == 4, variables
            return STATES
        return {}

    def close(self) -> None:
        pass


class Resolving(LinearProvider):
    """The provider with its transport replaced."""

    def __init__(self) -> None:
        super().__init__({"team_id": "x"}, {"token": "t"})
        self.fake = FakeHttp()

    @property
    def http(self) -> Any:
        return self.fake


class TeamResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = Resolving()

    def test_a_uuid_is_used_as_is(self) -> None:
        self.assertEqual(TEAM_UUID, self.provider._team(TEAM_UUID))
        self.assertEqual([], self.provider.fake.calls, "a UUID should need no lookup")

    def test_the_team_key_resolves(self) -> None:
        """What the Linear sidebar actually shows you."""
        self.assertEqual(TEAM_UUID, self.provider._team("OPS"))

    def test_the_team_name_resolves(self) -> None:
        self.assertEqual(TEAM_UUID, self.provider._team("Platform"))

    def test_matching_ignores_case(self) -> None:
        self.assertEqual(TEAM_UUID, self.provider._team("ops"))
        self.assertEqual(TEAM_UUID, self.provider._team("platform"))

    def test_a_key_wins_over_another_teams_name(self) -> None:
        """Keys are unique by construction; names are not."""
        self.assertEqual("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", self.provider._team("ENG"))

    def test_an_unknown_team_lists_the_real_ones(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider._team("Marketing")

        self.assertIn("Marketing", str(caught.exception))
        hint = str(caught.exception.hint)
        self.assertIn("ops", hint)
        self.assertIn("engineering", hint)

    def test_the_team_list_is_fetched_once(self) -> None:
        for _ in range(5):
            self.provider._team("OPS")
            self.provider._team("Platform")

        lookups = [call for call in self.provider.fake.calls if "teams (" in call]
        self.assertEqual(1, len(lookups))

    def test_columns_resolve_the_label_before_querying(self) -> None:
        """The whole point: a name entered in .env has to reach the API as a UUID."""
        columns = self.provider.list_columns("Platform")

        self.assertEqual(["Todo", "In Progress"], [column.name for column in columns])

    def test_an_empty_team_is_passed_through(self) -> None:
        """Let the API give its own error rather than inventing one here."""
        self.assertEqual("", self.provider._team(""))

    def test_live_team_with_null_description_maps_to_an_empty_description(self) -> None:
        """Linear returns null when a team has no description."""

        team = TeamWire.model_validate(
            {
                "id": TEAM_UUID,
                "key": "OPS",
                "name": "Platform",
                "description": None,
            }
        )

        self.assertEqual("", team_to_remote(team).description)

    def test_live_issue_with_null_description_maps_to_an_empty_body(self) -> None:
        """The same null-for-empty quirk, on an issue rather than a team --
        found live: a real team with even one undescribed issue failed this
        model's validation entirely, on every sync, not just at that issue."""

        issue = IssueWire.model_validate(
            {
                "id": "issue-1",
                "identifier": "OPS-1",
                "title": "No description set",
                "description": None,
            }
        )

        self.assertEqual("", issue_to_remote(issue).body)


class UuidShapeTests(unittest.TestCase):
    def test_a_real_uuid(self) -> None:
        self.assertTrue(_is_uuid(TEAM_UUID))

    def test_a_key_is_not_a_uuid(self) -> None:
        self.assertFalse(_is_uuid("OPS"))
        self.assertFalse(_is_uuid("Platform"))

    def test_near_misses_are_not_uuids(self) -> None:
        self.assertFalse(_is_uuid("aaaa1111-bbbb-2222-cccc"))  # too few groups
        self.assertFalse(_is_uuid("aaaa1111bbbb2222cccc333344445555"))  # no dashes
        self.assertFalse(_is_uuid("zzzz1111-bbbb-2222-cccc-333344445555"))  # not hex
        self.assertFalse(_is_uuid(""))

    def test_uppercase_hex_is_still_a_uuid(self) -> None:
        self.assertTrue(_is_uuid(TEAM_UUID.upper()))


class PriorityTests(unittest.TestCase):
    """Linear reports "no priority" as a label rather than as an absence."""

    def test_no_priority_becomes_empty(self) -> None:
        from pykantui.providers.linear import _priority

        for given in ("No priority", "no priority", "None", ""):
            self.assertEqual("", _priority({"priorityLabel": given}), given)

    def test_a_real_priority_is_kept(self) -> None:
        from pykantui.providers.linear import _priority

        self.assertEqual("Urgent", _priority({"priorityLabel": "Urgent"}))


if __name__ == "__main__":
    unittest.main()
