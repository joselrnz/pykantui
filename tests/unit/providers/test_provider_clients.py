"""Every provider owns its authentication-specific API client."""

from __future__ import annotations

import unittest
from typing import Protocol, cast

from pykantui.api import JsonHttp
from pykantui.tracker import get


class _HasHttp(Protocol):
    """Structural type for providers backed by the shared HTTP client."""

    @property
    def http(self) -> JsonHttp: ...


class ProviderClientStructureTests(unittest.TestCase):
    SETTINGS = {
        "asana": ({}, {"token": "test-token"}),
        "clickup": ({}, {"token": "test-token"}),
        "github": ({}, {"token": "test-token"}),
        "jira": ({}, {"base_url": "https://example.atlassian.net", "email": "dev@example.test", "token": "test"}),
        "linear": ({}, {"token": "test-token"}),
        "monday": ({}, {"token": "test-token"}),
        "plane": ({"workspace": "demo"}, {"token": "test-token"}),
        "shortcut": ({}, {"token": "test-token"}),
        "trello": ({}, {"key": "test-key", "token": "test-token"}),
    }

    def test_every_provider_builds_its_own_client_class(self) -> None:
        for name, (config, secrets) in self.SETTINGS.items():
            provider = get(name)(config, secrets)
            try:
                client = cast(_HasHttp, provider).http
                with self.subTest(provider=name):
                    self.assertIsInstance(client, JsonHttp)
                    self.assertEqual(f"pykantui.providers.{name}.client", type(client).__module__)
            finally:
                provider.close()


if __name__ == "__main__":
    unittest.main()
