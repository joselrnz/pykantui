"""End-to-end smoke test for `kbn mcp serve --transport socket`.

The script is intentionally minimal and can be used as a local proof command
for the MCP socket connector.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession

_streamable_module = importlib.import_module("mcp.client.streamable_http")
streamable_http_client: Any = getattr(_streamable_module, "streamable_http_client", None)
if streamable_http_client is None:  # pragma: no cover - compatibility with older MCP naming
    streamable_http_client = _streamable_module.streamablehttp_client

ROOT = Path(
    os.environ.get(
        "MCP_SMOKE_ROOT",
        str(Path(__file__).resolve().parents[1]),
    )
)
ENDPOINT = os.environ.get("MCP_SMOKE_ENDPOINT", "http://127.0.0.1:9010/mcp")
PORT = 9010
_READ_ONLY_TOOLS = frozenset({"list_workspaces", "list_cards"})


def _coerce_tool_payload(raw: object) -> list[dict[str, object]]:
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [dict(raw)]
    raise TypeError(f"unsupported tool payload: {type(raw)}")


def _coerce_from_tool(content: Sequence[object]) -> list[dict[str, object]]:
    texts = [text for item in content if isinstance(text := getattr(item, "text", None), str)]
    if not texts:
        return []
    payloads = json.loads(texts[0]) if len(texts) == 1 else [json.loads(raw) for raw in texts]
    return _coerce_tool_payload(payloads)


async def _call_read_only(client: Any, tool_name: str, arguments: dict[str, object]) -> Any:
    """Call only inspection tools so this smoke test cannot alter a real board."""

    if tool_name not in _READ_ONLY_TOOLS:
        raise ValueError(f"tool {tool_name!r} is not allowed in the read-only MCP smoke test")
    return await client.call_tool(tool_name, arguments)


async def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.environ.get("PYTHONPATH", str(ROOT / "src"))
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pykantui",
            "mcp",
            "serve",
            "--transport",
            "socket",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        started = time.time()
        while time.time() - started < 10:
            if proc.poll() is not None:
                print("server exited early:", proc.poll())
                return 1
            try:
                async with streamable_http_client(ENDPOINT) as streams:
                    read, write, _ = streams
                    async with ClientSession(read, write):
                        break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        else:
            print("server did not become ready within timeout")
            return 1

        async with streamable_http_client(ENDPOINT) as streams:
            read, write, _ = streams
            async with ClientSession(read, write) as client:
                await client.initialize()
                workspaces = await _call_read_only(client, "list_workspaces", {})
                workspace_payload = _coerce_from_tool(workspaces.content)
                print(f"registered_workspaces: {len(workspace_payload)}")
                print(
                    "providers:",
                    ", ".join(sorted(str(entry.get("provider", "unknown")) for entry in workspace_payload)),
                )
                for idx, entry in enumerate(workspace_payload, start=1):
                    print(
                        "workspace",
                        idx,
                        entry.get("provider"),
                        entry.get("key") or entry.get("name"),
                        "| project_id=",
                        entry.get("project_id"),
                    )
                providers = [str(entry["provider"]) for entry in workspace_payload]
                print("providers", sorted(providers))

                target_workspace = str(workspace_payload[0]["path"])
                cards = await _call_read_only(client, "list_cards", {"workspace": target_workspace})
                card_payload = _coerce_from_tool(cards.content)
                print("cards_in_target", len(card_payload))
                if not card_payload:
                    print("no cards found in target workspace")
                    return 0
                first = card_payload[0]
                print("first_card", first.get("id"), first.get("column"), first.get("title"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
