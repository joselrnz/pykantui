# The MCP server

`kbn mcp serve` runs pykantui as a local [MCP](https://modelcontextprotocol.io)
server: one tool surface, callable identically by any MCP-aware AI coding
tool — Claude Code, Cursor, Copilot Chat, Codex — instead of a prompt ported
by hand into each tool's own convention. An agent creates cards, sets
dependencies between them, assigns cards to other agents, moves them through
columns, and previews or sends a real sync — all through the same calls a
human would eventually confirm through `kbn` itself.

## Install and run

```bash
pip install pykantui[mcp]
kbn mcp serve

# Streamable HTTP socket mode (what most clients use in remote
# terminal/development setups):
kbn mcp serve --transport socket --host 127.0.0.1 --port 9010
```

The base install does not pull in the MCP SDK — `mcp` is an optional extra,
imported lazily by `kbn mcp`, so a plain `pip install pykantui` still works
without it. `kbn mcp serve` defaults to stdio. You can also run it over
`--transport sse` or streamable-HTTP with `--transport socket` (alias for
`streamable-http`) for long-lived connector sessions.

## Connecting a client

For a local command connector, let the client launch pykantui over stdio with
`kbn mcp serve`. For a long-lived HTTP connector, start the server separately:

```bash
kbn mcp serve --transport socket --host 127.0.0.1 --port 9010
```

The JSON shape differs slightly per client:

**Claude Code** (`.mcp.json` at the project root, or `claude mcp add`):

```json
{
  "mcpServers": {
    "pykantui": {
      "type": "stdio",
      "command": "kbn",
      "args": ["mcp", "serve"]
    }
  }
}
```

If the socket server above is already running, configure Claude Code with its
HTTP endpoint instead of a command:

```json
{
  "mcpServers": {
    "pykantui": {
      "type": "http",
      "url": "http://127.0.0.1:9010/mcp"
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`) — the same shape as Claude Code:

```json
{
  "mcpServers": {
    "pykantui": { "command": "kbn", "args": ["mcp", "serve"] }
  }
}
```

**VS Code / Copilot Chat** (`.vscode/mcp.json`) — a different root key, and
each server needs an explicit `type`. MCP tools only surface in Agent Mode:

```json
{
  "servers": {
    "pykantui": { "type": "stdio", "command": "kbn", "args": ["mcp", "serve"] }
  }
}
```

**Codex CLI** (`~/.codex/config.toml` or a project `.codex/config.toml`):

```toml
[mcp_servers.pykantui]
command = "kbn"
args = ["mcp", "serve"]
```

## The tool surface

Every tool takes an explicit `workspace` path — never the current directory,
never inferred. A workspace and the project it plans work for (a git repo
being built, say) are ordinarily two different directories; nothing here
`cd`s into either.

### Read tools — no side effects

| Tool | What it returns |
| --- | --- |
| `list_workspaces()` | every workspace registered on this machine |
| `list_cards(workspace?, column?, assigned_agent?)` | cards, filtered; omit `workspace` to search every registered one |
| `get_card(workspace, card_id)` | full detail for one card, by issue id or tracker key |
| `list_dependencies(workspace, card_id)` | a card's `blocked-by` list and its agent assignment |

### Local mutations — immediate, git-committed, no dry run

These only ever touch local Markdown, so they carry none of the ceremony the
provider boundary below needs. Each ends in its own commit (message prefixed
`mcp(<agent_name>): ...`) when the workspace is a Git repository — skipped
silently on a `kbn init --no-git` workspace, and reported as a warning rather
than raised if Git is present but the commit itself fails, since the file
write has already succeeded by that point.

| Tool | Effect |
| --- | --- |
| `create_card(workspace, title, …)` | draft a card — `column`, `issue_type`, `body`, `labels`, `parent`, `blocked_by`, `assigned_agent` all optional |
| `move_card(workspace, card_id, column)` | move a card, refused with a clear reason if an unfinished `blocked_by` entry is still open |
| `set_dependency(workspace, card_id, blocked_by)` | replace a card's blocker list; an empty list clears it |
| `assign_agent(workspace, card_id, assigned_agent)` | record which agent owns a card |
| `update_card(workspace, card_id, …)` | edit `title`, `issue_type`, `labels`, `due`, `priority`, `body` — never `assignee`, see below |

### The one hard-gated boundary: the provider push

Client-side approval UI (tool-annotation hints, elicitation) isn't reliably
supported across every target client, so this is a server-side contract
instead of something borrowed from the caller's own interface:

- **`preview_sync(workspace)`** — what a sync would send. Sends nothing.
  Returns a summary and a token.
- **`confirm_sync(workspace, token)`** — sends exactly that plan, only if the
  token still matches both what was issued *and* what the workspace looks
  like right now. Reuses the same code path `kbn sync --yes` already proves.

A token is invalidated by any subsequent mutation to that workspace, not
purely by time — call `preview_sync` again after any `create_card`/
`move_card`/etc. and confirm the fresh token it returns.

**This is the whole answer to running supervised or unsupervised.** An
autonomous agent chains every tool including `confirm_sync` itself, with no
pause. A human-supervised setup shows `preview_sync`'s summary to a person
and only calls `confirm_sync` after they approve. The tools are identical
either way — only who calls `confirm_sync`, and when, differs.

## Cards, dependencies, and agents on disk

Dependencies and agent assignment are local-only metadata, stored in a new
Markdown marker — `pykantui:agent`, documented in full in
[`markdown-format.md`](markdown-format.md#the-pykantuiagent-marker). Never
read from or sent to a provider, exactly like `pykantui:notes`.

This is deliberately not the tracker's real `assignee` field: `assignee` is
pushed back to the provider on a confirmed sync, and an unrecognized agent
name there would either fail provider validation or silently overwrite a
human's real assignment. `assign_agent` and `update_card` are kept strictly
separate for exactly this reason — `update_card` can never touch `assignee`.

### A known limitation: dependencies across a draft's first sync

A `blocked_by` reference is a plain string — a card's issue id (its filename
stem before it has ever synced) or its tracker key afterward. Setting a
dependency on a still-unsynced draft, then syncing, means the draft's id
changes to its real key — but nothing rewrites the *reference* to it held by
whatever depends on it. The old id no longer resolves to anything, and
`move_card`'s gate fails open on an unresolvable blocker id rather than
blocking forever on a typo, so the dependency is silently dropped rather than
erroring.

Practical rule: set dependencies **after** an initial sync has given both
cards real keys, or re-run `set_dependency` once it has. `list_dependencies`
is cheap — check it after a sync involving cards with dependencies to confirm
they still resolve as expected.

## What this does not do

**No write-back into a third repository.** Cards move through columns; a
target project's own files (a spec, a task list, anything outside the
pykantui workspace) are never touched by any tool here.

**No exclusive "claim" on `assign_agent`.** It records who's supposed to
work a card, the same way a human assignee is informational on a real
tracker — it does not stop two agents from both acting on the same card.
Coordinating that is a deliberate non-goal of this server, not a gap to be
filled quietly later.

## Related

- [`markdown-format.md`](markdown-format.md) — the file format `pykantui:agent` extends
- [`provider-architecture.md`](provider-architecture.md) — how a provider workspace works underneath
