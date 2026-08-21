"""pykantui as an MCP server -- cards, dependencies, and agent assignment for
any MCP-aware AI coding tool (Claude Code, Cursor, Copilot Chat, Codex).

Local mutations (create/move/set-dependency/assign/update) are immediate and
git-committed, matching how pykantui already treats local Markdown writes as
cheap and reversible. The one hard-gated boundary is the provider push, via a
``preview_sync``/``confirm_sync`` token pair -- see ``tokens.py``. See
``docs/mcp.md`` for the full tool surface and per-client setup.
"""

from __future__ import annotations
