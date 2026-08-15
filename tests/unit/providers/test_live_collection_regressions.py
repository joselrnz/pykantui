"""Regressions found by the run-tagged nine-provider live certification."""

from __future__ import annotations

import unittest
from typing import cast
from unittest.mock import Mock

from pykantui.api import JsonValue, PayloadError
from pykantui.providers.monday.provider import MondayProvider
from pykantui.providers.shortcut.client import ShortcutApi
from pykantui.providers.shortcut.routes import search_continuation
from pykantui.tracker.models import IssueDraft


class _ShortcutPages:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get(self, path: str, params: object = None, **kwargs: object) -> JsonValue:
        del kwargs
        self.calls.append((path, params))
        if len(self.calls) == 1:
            return {
                "data": [{"id": 1, "name": "first"}],
                "next": "/api/v3/search/stories?query=workflow%3A%22Standard%22&next=opaque~24",
            }
        if path == "/search/stories?query=workflow%3A%22Standard%22&next=opaque~24":
            return {"data": [{"id": 2, "name": "second"}], "next": None}
        raise AssertionError(f"unexpected continuation path: {path}")


class ShortcutPaginationRegressionTests(unittest.TestCase):
    def test_provider_continuation_does_not_duplicate_the_base_api_prefix(self) -> None:
        transport = _ShortcutPages()
        api = ShortcutApi(cast(object, transport))  # type: ignore[arg-type]

        stories = list(api.stories('workflow:"Standard"', page_size=25))

        self.assertEqual([1, 2], [story.id for story in stories])
        self.assertEqual(
            "/search/stories?query=workflow%3A%22Standard%22&next=opaque~24",
            transport.calls[1][0],
        )
        self.assertIsNone(transport.calls[1][1])

    def test_continuation_must_stay_on_the_story_search_endpoint(self) -> None:
        unsafe = (
            "https://attacker.invalid/collect",
            "/api/v3/members?next=opaque",
            "/api/v3/search/stories",
            "/api/v3/api/v3/search/stories?next=opaque",
        )
        for value in unsafe:
            with self.subTest(value=value), self.assertRaises(PayloadError):
                search_continuation(value)

    def test_already_relative_continuation_remains_usable(self) -> None:
        value = "/search/stories?query=workflow%3A%22Standard%22&next=opaque"

        self.assertEqual(value, search_continuation(value))


class MondayCreateRegressionTests(unittest.TestCase):
    def test_auto_detected_status_axis_is_used_instead_of_local_slug_as_group(self) -> None:
        provider = MondayProvider({"board_id": "18425778690"}, {"token": "secret"})
        provider._status_axis = Mock(  # type: ignore[method-assign]
            return_value=("project_status", {"5": "Not Started"})
        )

        payload = provider.build_create_payload(
            "18425778690",
            IssueDraft(title="canary", column_id="5"),
        )

        self.assertIsNone(payload["group"])
        self.assertEqual('{"project_status": {"index": 5}}', payload["values"])


if __name__ == "__main__":
    unittest.main()
