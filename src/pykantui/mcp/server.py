"""The pykantui MCP server: ``kbn mcp serve``.

One tool surface, callable identically by any MCP-aware client (Claude Code,
Cursor, Copilot Chat, Codex). Local mutations (create/move/set-dependency/
assign/update) are immediate and git-committed -- see ``cards.py``. The one
hard-gated boundary is the provider push: ``preview_sync`` returns a summary
and a token; ``confirm_sync`` only proceeds if that exact token is presented,
reusing the same non-interactive path ``kbn sync --yes`` already proves
(``confirm=None`` auto-approves inside ``workspace.sync.sync``).

An autonomous agent chains every tool including ``confirm_sync`` itself,
unsupervised. A human-supervised client shows ``preview_sync``'s summary to a
person first and only calls ``confirm_sync`` after they approve. Nothing
about the tools differs between the two -- only who calls ``confirm_sync``
and when.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from typing_extensions import TypedDict

from pykantui.tracker.errors import ProviderError
from pykantui.workspace import sync as workspace_sync
from pykantui.workspace.models import SyncPlan
from pykantui.workspace.registry import load_registry

from . import cards, dependencies, tokens
from .cards import CardDetail, CardSummary, open_workspace, resolve_workspace

mcp = FastMCP("pykantui")


class WorkspaceSummary(TypedDict):
    provider: str
    project_id: str
    key: str
    name: str
    path: str


class DependencySummary(TypedDict):
    card: str
    blocked_by: list[str]
    assigned_agent: str


class SyncPreview(TypedDict):
    summary: str
    creates: int
    pushes: int
    token: str


# ---- read tools -------------------------------------------------------------


@mcp.tool()
def list_workspaces() -> list[WorkspaceSummary]:
    """List every pykantui workspace registered on this machine."""
    return [
        WorkspaceSummary(
            provider=link.provider,
            project_id=link.project_id,
            key=link.key,
            name=link.name,
            path=link.workspace,
        )
        for link in load_registry().projects
    ]


@mcp.tool()
def list_cards(
    workspace: str | None = None,
    column: str | None = None,
    assigned_agent: str | None = None,
) -> list[CardSummary]:
    """List cards. ``workspace=None`` searches every registered workspace."""
    paths = [workspace] if workspace is not None else [link.workspace for link in load_registry().projects]
    results: list[CardSummary] = []
    for path in paths:
        with open_workspace(path) as ws:
            for entry in ws.on_disk.values():
                summary = cards.summarize_card(entry)
                if column and summary["column"].lower() != column.lower():
                    continue
                # Case-insensitive exact match, not substring -- "codex" must
                # not also match a card assigned to "codex-review".
                if assigned_agent and summary["assigned_agent"].lower() != assigned_agent.lower():
                    continue
                results.append(summary)
    return results


@mcp.tool()
def get_card(workspace: str, card_id: str) -> CardDetail:
    """Full detail for one card, by its issue id or tracker key."""
    with open_workspace(workspace) as ws:
        entry = dependencies.resolve_card(ws.on_disk, card_id)
        if entry is None:
            raise ProviderError(f"no card matching {card_id!r} in this workspace")
        return cards.detail_card(entry)


@mcp.tool()
def list_dependencies(workspace: str, card_id: str) -> DependencySummary:
    """A card's ``blocked-by`` list and its agent assignment."""
    with open_workspace(workspace) as ws:
        entry = dependencies.resolve_card(ws.on_disk, card_id)
        if entry is None:
            raise ProviderError(f"no card matching {card_id!r} in this workspace")
        summary = cards.summarize_card(entry)
        return DependencySummary(
            card=card_id, blocked_by=summary["blocked_by"], assigned_agent=summary["assigned_agent"]
        )


# ---- local mutations ---------------------------------------------------------


@mcp.tool()
def create_card(
    workspace: str,
    title: str,
    column: str = "",
    issue_type: str = "",
    body: str = "",
    labels: Sequence[str] = (),
    parent: str = "",
    blocked_by: Sequence[str] = (),
    assigned_agent: str = "",
    agent_name: str = "mcp",
) -> CardSummary:
    """Draft a card. Local only -- see ``preview_sync``/``confirm_sync``."""
    return cards.create_card(
        workspace,
        title,
        column=column,
        issue_type=issue_type,
        body=body,
        labels=labels,
        parent=parent,
        blocked_by=blocked_by,
        assigned_agent=assigned_agent,
        agent_name=agent_name,
    )


@mcp.tool()
def move_card(workspace: str, card_id: str, column: str, agent_name: str = "mcp") -> CardSummary:
    """Move a card, refused if its own ``blocked-by`` list isn't clear."""
    return cards.move_card(workspace, card_id, column, agent_name=agent_name)


@mcp.tool()
def set_dependency(workspace: str, card_id: str, blocked_by: Sequence[str], agent_name: str = "mcp") -> CardSummary:
    """Replace a card's ``blocked-by`` list. An empty list clears it."""
    return cards.set_dependency(workspace, card_id, blocked_by, agent_name=agent_name)


@mcp.tool()
def assign_agent(workspace: str, card_id: str, assigned_agent: str, agent_name: str = "mcp") -> CardSummary:
    """Record which agent owns a card. Informational, not an exclusive claim."""
    return cards.assign_agent(workspace, card_id, assigned_agent, agent_name=agent_name)


@mcp.tool()
def update_card(
    workspace: str,
    card_id: str,
    title: str | None = None,
    issue_type: str | None = None,
    labels: Sequence[str] | None = None,
    due: str | None = None,
    priority: str | None = None,
    body: str | None = None,
    agent_name: str = "mcp",
) -> CardSummary:
    """Edit a card's title/type/labels/due/priority/body. Never ``assignee``
    -- that stays reserved for real tracker identity via a confirmed sync."""
    return cards.update_card(
        workspace,
        card_id,
        title=title,
        issue_type=issue_type,
        labels=labels,
        due=due,
        priority=priority,
        body=body,
        agent_name=agent_name,
    )


# ---- the one hard-gated boundary: the provider push --------------------------


@mcp.tool()
def preview_sync(workspace: str) -> SyncPreview:
    """What a sync would send. Sends nothing. Returns a token for confirm_sync."""
    path = resolve_workspace(workspace)
    with open_workspace(workspace) as ws:
        plan = workspace_sync.preview(path, ws.provider, ws.project.remote(), column_style=ws.project.column_style)
    token = tokens.issue(path, plan)
    return SyncPreview(
        summary=plan.describe(),
        creates=len(plan.creates),
        pushes=len(plan.clean()),
        token=token,
    )


@mcp.tool()
def confirm_sync(workspace: str, token: str) -> str:
    """Send exactly the plan ``preview_sync`` returned this token for.

    Refused if the token is stale, unknown, or the workspace changed since
    the preview -- re-run ``preview_sync`` and confirm the new token instead.
    This is pykantui's one call that reaches a real tracker; every other tool
    in this server only ever touches local Markdown.
    """
    path = resolve_workspace(workspace)
    with open_workspace(workspace) as ws:
        plan = workspace_sync.preview(path, ws.provider, ws.project.remote(), column_style=ws.project.column_style)
        _require_valid_sync_token(path, token, plan)

        def confirm_locked_plan(locked_plan: SyncPlan) -> bool:
            # ``workspace_sync.sync`` invokes this while holding the workspace
            # lock. The preflight above gives a fast error; this second check
            # is the actual provider-write gate and closes the check/use race.
            _require_valid_sync_token(path, token, locked_plan)
            return True

        report = workspace_sync.sync(
            path,
            ws.provider,
            ws.project.remote(),
            column_style=ws.project.column_style,
            confirm=confirm_locked_plan,
        )
    tokens.invalidate(path)
    return report.summary()


def _require_valid_sync_token(path: Path, token: str, plan: SyncPlan) -> None:
    if not tokens.verify(path, token, plan):
        raise ProviderError(
            "sync token is stale or does not match the current plan",
            hint="Call preview_sync again and confirm the token it returns.",
        )
