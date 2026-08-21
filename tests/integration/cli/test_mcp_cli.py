"""CLI parser and runner coverage for `kbn mcp`.

The transport options are part of onboarding; adding explicit tests here keeps the
socket mode contract from regressing silently.
"""

from __future__ import annotations

import json
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pykantui.cli.main import build_parser
from pykantui.commands import mcp as mcp_command


class McpCommandTests(unittest.TestCase):
    """Keep transport parsing behavior stable for every supported MCP client."""

    @staticmethod
    def args(**overrides: object) -> Namespace:
        base = Namespace(
            mcp_command="serve",
            transport="stdio",
            host=None,
            port=None,
            mount_path=None,
        )
        base.__dict__.update(overrides)
        return base

    def test_parser_has_transport_host_port_mount_args(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["mcp", "serve", "--transport", "socket", "--host", "127.0.0.1", "--port", "9012"])
        self.assertIsNotNone(parsed)
        assert parsed.transport == "socket"
        assert parsed.host == "127.0.0.1"
        assert parsed.port == 9012

    def test_parser_allows_mcp_socket_alias_with_mount_path(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["mcp", "serve", "--transport", "socket", "--mount-path", "/pykantui"])
        self.assertIsNotNone(parsed)
        assert parsed.mount_path == "/pykantui"

    def test_socket_runs_streamable_http_with_updated_host_port(self) -> None:
        fake_server = MagicMock()
        fake_server.settings = SimpleNamespace(
            host="127.0.0.1",
            port=8000,
            mount_path="/",
            streamable_http_path="/mcp",
        )

        with patch("pykantui.mcp.server.mcp", fake_server):
            code = mcp_command.run(self.args(transport="socket", host="0.0.0.0", port=9001, mount_path="/mcp-socket"))

        self.assertEqual(0, code)
        assert fake_server.settings.host == "0.0.0.0"
        assert fake_server.settings.port == 9001
        assert fake_server.settings.mount_path == "/mcp-socket"
        assert fake_server.settings.streamable_http_path == "/mcp-socket"
        fake_server.run.assert_called_once_with("streamable-http")

    def test_sse_runs_with_mount_path(self) -> None:
        fake_server = MagicMock()
        fake_server.settings = SimpleNamespace(host="127.0.0.1", port=8000, mount_path="/", streamable_http_path="/mcp")

        with patch("pykantui.mcp.server.mcp", fake_server):
            code = mcp_command.run(self.args(transport="sse", mount_path="/sse-path"))

        self.assertEqual(0, code)
        fake_server.run.assert_called_once_with("sse", mount_path="/sse-path")

    def test_checked_in_command_connector_uses_stdio(self) -> None:
        root = Path(__file__).resolve().parents[3]
        document = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        connector = document["mcpServers"]["pykantui"]

        self.assertEqual("stdio", connector["type"])
        self.assertEqual("kbn", connector["command"])
        self.assertEqual(["mcp", "serve"], connector["args"])
