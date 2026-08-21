"""The ``preview_sync`` / ``confirm_sync`` handshake.

MCP client-side approval UI (tool-annotation hints, elicitation) is not
reliably supported across every target client, so pykantui's "never write to
a provider without confirmation" principle has to be a server-side contract
instead: ``preview_sync`` issues a token, ``confirm_sync`` only proceeds if
the token matches both what was stored *and* what the plan looks like right
now. On-disk rather than in-memory -- ``kbn mcp serve`` is a per-session
subprocess, and nothing stops a second client, or a restart between preview
and confirm, from invalidating an in-memory token silently.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from typing_extensions import TypedDict

from pykantui.config.paths import write_text_atomic
from pykantui.workspace import layout
from pykantui.workspace.locking import exclusive_workspace
from pykantui.workspace.models import SyncPlan
from pykantui.workspace.paths import ensure_workspace_path

TOKEN_FILE = "mcp-sync-token.json"

#: A backstop only. The rule that actually matters is invalidate-on-mutation
#: (every mutation tool calls :func:`invalidate`); this just bounds how long
#: a token started and then abandoned stays valid.
EXPIRY_SECONDS = 600


class _StoredToken(TypedDict):
    plan_hash: str
    issued_at: float


def plan_hash(plan: SyncPlan) -> str:
    """A stable fingerprint of what a sync would send.

    Built from :meth:`SyncPlan.outbound_token`, the plan's own "stable, exact
    identity for this plan's writes" -- reused rather than re-derived, so
    this hash changes exactly when a sync's actual outbound effect would.
    """
    material = repr(plan.outbound_token())
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def issue(workspace: Path, plan: SyncPlan) -> str:
    """Write a fresh token for ``plan``, replacing any prior one. Returns it."""
    digest = plan_hash(plan)
    with exclusive_workspace(workspace):
        path = ensure_workspace_path(workspace, _token_path(workspace))
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps({"plan_hash": digest, "issued_at": time.time()}))
    return digest


def verify(workspace: Path, token: str, plan: SyncPlan) -> bool:
    """Whether ``token`` matches the stored token *and* the plan as of now.

    Both checks matter: the stored-token check catches a stale or invented
    token; recomputing the plan's hash catches drift -- a second agent, or a
    manual edit, changing the workspace between preview and confirm.
    """
    stored = _read(workspace)
    if stored is None:
        return False
    if time.time() - stored["issued_at"] > EXPIRY_SECONDS:
        return False
    if stored["plan_hash"] != token:
        return False
    return plan_hash(plan) == token


def invalidate(workspace: Path) -> None:
    """Drop any pending token. Called by every mutation tool.

    This, not the time-based expiry, is the rule that actually keeps
    ``confirm_sync`` honest: a token issued before ``move_card`` ran must
    never apply after it.
    """
    ensure_workspace_path(workspace, _token_path(workspace)).unlink(missing_ok=True)


def _token_path(workspace: Path) -> Path:
    return layout.cache_dir(workspace) / TOKEN_FILE


def _read(workspace: Path) -> _StoredToken | None:
    try:
        raw = ensure_workspace_path(workspace, _token_path(workspace)).read_text(encoding="utf-8")
        document = json.loads(raw)
        return _StoredToken(plan_hash=str(document["plan_hash"]), issued_at=float(document["issued_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
