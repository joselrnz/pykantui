"""Plane create responses are sparse/expanded compared with list responses."""

from __future__ import annotations

import unittest

from pykantui.providers.plane.mapper import work_item_to_remote
from pykantui.providers.plane.schemas import WorkItemWire


class PlaneCreateResponseTests(unittest.TestCase):
    def test_nullable_and_expanded_create_response_maps_to_a_canonical_issue(self) -> None:
        wire = WorkItemWire.model_validate(
            {
                "id": "work-1",
                "sequence_id": 27,
                "name": "Created card",
                "state": {"id": "state-1", "name": "Todo", "group": "unstarted"},
                "description_html": None,
                "description_stripped": None,
                "priority": None,
                "assignees": None,
                "created_by": {"id": "member-1", "display_name": "Alex"},
                "labels": None,
            }
        )

        issue = work_item_to_remote(
            wire,
            workspace="acme",
            project_id="project-1",
            identifier="OPS",
            states={"state-1": "Todo"},
            members={"member-1": "Alex"},
            labels=[],
        )

        self.assertEqual("work-1", issue.issue_id)
        self.assertEqual("OPS-27", issue.key)
        self.assertEqual("state-1", issue.column_id)
        self.assertEqual("Todo", issue.status)
        self.assertEqual("Alex", issue.reporter)
        self.assertEqual("", issue.body)
        self.assertEqual("", issue.priority)
        self.assertEqual((), issue.assignee_ids)
        self.assertEqual((), issue.labels)


if __name__ == "__main__":
    unittest.main()
