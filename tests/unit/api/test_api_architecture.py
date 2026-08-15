"""Architecture contract for outbound provider API access."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any, get_type_hints


class ApiPackageTests(unittest.TestCase):
    def test_api_is_a_real_source_package(self) -> None:
        spec = importlib.util.find_spec("pykantui.api")

        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.submodule_search_locations if spec else None)

    def test_api_exports_the_shared_transport_contract(self) -> None:
        from pykantui.api import JsonClient, JsonHttp, ResponseCache, page_by_cursor

        self.assertTrue(callable(JsonClient))
        self.assertTrue(callable(JsonHttp))
        self.assertTrue(callable(ResponseCache))
        self.assertTrue(callable(page_by_cursor))

    def test_transport_implementations_are_owned_by_api(self) -> None:
        from pykantui.api import JsonHttp, ResponseCache, page_by_cursor

        self.assertEqual("pykantui.api.client", JsonHttp.__module__)
        self.assertEqual("pykantui.api.cache", ResponseCache.__module__)
        self.assertEqual("pykantui.api.pagination", page_by_cursor.__module__)

    def test_providers_do_not_import_transport_from_tracker(self) -> None:
        providers = Path(__file__).parents[3] / "src" / "pykantui" / "providers"

        for source in providers.glob("*/provider.py"):
            text = source.read_text(encoding="utf-8")
            with self.subTest(provider=source.parent.name):
                self.assertNotIn("pykantui.tracker.http", text)
                self.assertNotIn("pykantui.tracker.cache", text)

    def test_public_transport_methods_never_expose_any(self) -> None:
        from pykantui.api import JsonHttp

        for name in ("get", "post", "put", "patch", "graphql", "request"):
            with self.subTest(method=name):
                hints = get_type_hints(getattr(JsonHttp, name))
                self.assertNotIn(Any, hints.values())

    def test_github_package_separates_wire_and_domain_responsibilities(self) -> None:
        package = Path(__file__).parents[3] / "src" / "pykantui" / "providers" / "github"

        self.assertTrue({"routes.py", "schemas.py", "mapper.py", "payloads.py"}.issubset(
            {path.name for path in package.iterdir()}
        ))
        provider_source = (package / "provider.py").read_text(encoding="utf-8")
        self.assertNotIn("self.http.get(", provider_source)
        self.assertNotIn("self.http.post(", provider_source)
        self.assertNotIn("self.http.put(", provider_source)
        self.assertNotIn("self.http.patch(", provider_source)


if __name__ == "__main__":
    unittest.main()
