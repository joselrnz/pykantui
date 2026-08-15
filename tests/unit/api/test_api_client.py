"""Focused transport behavior that every provider relies on."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import BaseModel

from pykantui.api import (
    JsonHttp,
    PayloadError,
    ProviderError,
    ResponseCache,
    RetryPolicy,
    TransportError,
    expect_array,
    expect_object,
    expect_object_array,
    parse_json,
)


class _WireItem(BaseModel):
    item_id: int
    title: str


class RetryPolicyTests(unittest.TestCase):
    def test_exponential_delay_is_bounded(self) -> None:
        policy = RetryPolicy(retries=4, base_delay=2.0, max_delay=5.0)

        self.assertEqual([2.0, 4.0, 5.0, 5.0], [policy.delay(attempt) for attempt in range(4)])

    def test_provider_retry_after_wins_but_is_still_bounded(self) -> None:
        policy = RetryPolicy(max_delay=30.0)

        self.assertEqual(12.0, policy.delay(0, retry_after=12.0))
        self.assertEqual(30.0, policy.delay(0, retry_after=120.0))

    def test_negative_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RetryPolicy(retries=-1)


class JsonHttpRetryTests(unittest.TestCase):
    @staticmethod
    def _transport(respond: httpx.MockTransport) -> JsonHttp:
        client = httpx.Client(base_url="https://example.test", transport=respond)
        return JsonHttp(
            "https://example.test",
            retry_policy=RetryPolicy(retries=1, base_delay=0),
            sleeper=lambda _delay: None,
            client=client,
        )

    def test_rate_limit_retries_once_and_honours_retry_after(self) -> None:
        requests = 0
        sleeps: list[float] = []

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond))
        transport = JsonHttp(
            "https://example.test",
            retry_policy=RetryPolicy(retries=1),
            sleeper=sleeps.append,
            client=client,
        )

        try:
            self.assertEqual({"ok": True}, transport.get("/items"))
        finally:
            transport.close()

        self.assertEqual(2, requests)
        self.assertEqual([7.0], sleeps)

    def test_get_retries_a_server_error(self) -> None:
        requests = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(503 if requests == 1 else 200, json={"ok": True}, request=request)

        transport = self._transport(httpx.MockTransport(respond))
        try:
            self.assertEqual({"ok": True}, transport.get("/items"))
        finally:
            transport.close()
        self.assertEqual(2, requests)

    def test_get_retries_a_timeout(self) -> None:
        requests = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        transport = self._transport(httpx.MockTransport(respond))
        try:
            self.assertEqual({"ok": True}, transport.get("/items"))
        finally:
            transport.close()
        self.assertEqual(2, requests)

    def test_client_error_is_not_retried(self) -> None:
        requests = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(400, json={"message": "bad input"}, request=request)

        transport = self._transport(httpx.MockTransport(respond))
        try:
            with self.assertRaises(ProviderError):
                transport.get("/items")
        finally:
            transport.close()
        self.assertEqual(1, requests)

    def test_write_is_not_replayed_after_a_server_error(self) -> None:
        requests = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(503, request=request)

        transport = self._transport(httpx.MockTransport(respond))
        try:
            with self.assertRaises(TransportError):
                transport.post("/items", {"title": "one copy"})
        finally:
            transport.close()
        self.assertEqual(1, requests)

    def test_provider_error_cannot_echo_the_submitted_token_or_terminal_markup(self) -> None:
        token = "github_pat_super_secret_value"

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"message": f"received {token}\u001b[31m[bold]"},
                request=request,
            )

        client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond))
        transport = JsonHttp.with_bearer("https://example.test", token, client=client)
        try:
            with self.assertRaises(ProviderError) as caught:
                transport.get("/items")
        finally:
            transport.close()

        message = str(caught.exception)
        self.assertNotIn(token, message)
        self.assertNotIn("\u001b", message)
        self.assertNotIn("[bold]", message)
        self.assertIn("[REDACTED]", message)

    def test_graphql_error_cannot_echo_the_submitted_token(self) -> None:
        token = "linear-secret-token"

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errors": [{"message": f"bad token {token}"}]}, request=request)

        client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond))
        transport = JsonHttp.with_header_key("https://example.test", "Authorization", token, client=client)
        try:
            with self.assertRaises(ProviderError) as caught:
                transport.graphql("query { viewer { id } }")
        finally:
            transport.close()

        self.assertNotIn(token, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_authenticated_redirect_is_refused_without_contacting_the_target(self) -> None:
        requests: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://attacker.test/capture"}, request=request)

        client = httpx.Client(
            base_url="https://example.test",
            transport=httpx.MockTransport(respond),
            # A caller-provided test/client transport must not be able to
            # weaken JsonHttp's credential boundary.
            follow_redirects=True,
        )
        transport = JsonHttp.with_bearer("https://example.test", "secret", client=client)
        try:
            with self.assertRaisesRegex(ProviderError, "redirect"):
                transport.get("/items")
        finally:
            transport.close()

        self.assertEqual(["https://example.test/items"], requests)


class PayloadValidationTests(unittest.TestCase):
    def test_parse_json_builds_a_typed_pydantic_model(self) -> None:
        item = parse_json({"item_id": 7, "title": "Typed"}, _WireItem)

        self.assertEqual(_WireItem(item_id=7, title="Typed"), item)

    def test_parse_json_normalizes_malformed_provider_payloads(self) -> None:
        with self.assertRaises(PayloadError):
            parse_json({"item_id": "not-an-integer", "title": "Broken"}, _WireItem)

    def test_shape_helpers_reject_the_wrong_document_kind(self) -> None:
        self.assertEqual({"id": 7}, expect_object({"id": 7}))
        self.assertEqual([{"id": 7}], expect_array([{"id": 7}]))
        self.assertEqual([{"id": 7}], expect_object_array([{"id": 7}]))

        with self.assertRaisesRegex(PayloadError, "JSON object"):
            expect_object([])
        with self.assertRaisesRegex(PayloadError, "JSON array"):
            expect_array({})
        with self.assertRaisesRegex(PayloadError, "array item 1"):
            expect_object_array([{"id": 7}, "broken"])


class ConcurrentResponseCacheTests(unittest.TestCase):
    def test_cache_payload_uses_the_private_atomic_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "pykantui.api.cache.write_text_atomic"
        ) as write:
            ResponseCache(Path(directory)).scope("jira", "JPT").put("issues", [])

        self.assertTrue(write.call_args.kwargs["private"])

    @unittest.skipIf(os.name == "nt", "POSIX modes; Windows uses its native ACL behavior")
    def test_cache_payload_and_directories_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "cache"
            cache = ResponseCache(root).scope("jira", "JPT")

            cache.put("issues", [{"id": "private"}])

            self.assertEqual(0o600, cache.path_for("issues").stat().st_mode & 0o777)
            for path in (root, root / "jira", root / "jira" / "JPT"):
                with self.subTest(path=path):
                    self.assertEqual(0o700, path.stat().st_mode & 0o777)

    def test_concurrent_writers_leave_one_complete_json_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = ResponseCache.key_for("GET", "/types", {}, "issue-types")

            def write(number: int) -> None:
                ResponseCache(root).scope("jira", "JPT").put(key, {"writer": number})

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(write, range(64)))

            path = ResponseCache(root).scope("jira", "JPT").path_for(key)
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, document["schema"])
            self.assertIn(document["body"]["writer"], range(64))

if __name__ == "__main__":
    unittest.main()
