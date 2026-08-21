"""Safety contract for the developer-facing MCP transport smoke test."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from tools import mcp_socket_smoke


class McpSocketSmokeSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_call_allows_inspection_tools(self) -> None:
        client = AsyncMock()
        client.call_tool.return_value = "result"

        result = await mcp_socket_smoke._call_read_only(client, "list_cards", {"workspace": "board"})

        self.assertEqual("result", result)
        client.call_tool.assert_awaited_once_with("list_cards", {"workspace": "board"})

    async def test_read_only_call_refuses_mutation_tools(self) -> None:
        client = AsyncMock()

        with self.assertRaisesRegex(ValueError, "not allowed"):
            await mcp_socket_smoke._call_read_only(client, "move_card", {})

        client.call_tool.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
