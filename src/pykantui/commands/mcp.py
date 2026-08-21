"""``kbn mcp serve`` — run pykantui as a local MCP server.

Any MCP-aware client (Claude Code, Cursor, Copilot Chat, Codex) launches this
as a subprocess per its own config; see ``docs/mcp.md`` for the exact config
shape each expects. The ``mcp`` package is an optional dependency
(``pip install pykantui[mcp]``), so the import happens here, lazily, and not
at module load time -- the base CLI must keep working without it installed.
"""

from __future__ import annotations

import argparse
import sys

from pykantui.i18n import translate as _


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("run pykantui as an MCP server for AI coding agents")
    parser = sub.add_parser("mcp", help=summary, description=summary)
    verbs = parser.add_subparsers(dest="mcp_command", required=True)
    serve = verbs.add_parser("serve", help=_("start the server for AI clients"))
    serve.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http", "socket"),
        default="stdio",
        help="stdio (default), sse, streamable-http, or socket (alias for streamable-http)",
    )
    serve.add_argument(
        "--host",
        default=None,
        help="host for socket/SSE transport (default: 127.0.0.1)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="port for socket/SSE transport (default: 8000)",
    )
    serve.add_argument(
        "--mount-path",
        default=None,
        help="mount path for SSE and streamable-http transport",
    )


def run(args: argparse.Namespace) -> int:
    try:
        from pykantui.mcp.server import mcp  # noqa: PLC0415 - optional dependency, imported lazily
    except ModuleNotFoundError as error:
        print(f"error: the MCP server needs an extra pykantui was not installed with: {error}", file=sys.stderr)
        print("  pip install pykantui[mcp]", file=sys.stderr)
        return 2

    if args.host is not None:
        mcp.settings.host = args.host
    if args.port is not None:
        mcp.settings.port = args.port

    transport = args.transport
    if transport == "socket":
        transport = "streamable-http"
    if args.mount_path:
        mcp.settings.mount_path = args.mount_path
        mcp.settings.streamable_http_path = args.mount_path

    if transport == "sse":
        mcp.run("sse", mount_path=args.mount_path)
        return 0

    if transport in {"streamable-http", "socket"}:
        mcp.run("streamable-http")
        return 0

    mcp.run()
    return 0
