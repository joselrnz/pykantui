"""Sync two tagged edits plus one comment through the real TUI and verify API truth."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.live_provider_certification import CertificationContext, ReceiptLog, WritesDisabledError
else:
    try:
        from live_provider_certification import CertificationContext, ReceiptLog, WritesDisabledError
    except ModuleNotFoundError:  # imported as tools.live_tui_mutation_sync in unit tests
        from tools.live_provider_certification import CertificationContext, ReceiptLog, WritesDisabledError
from textual.widgets import Button

from pykantui.pages.sync import SyncConfirmScreen, SyncProgressScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tui.app import KanbanApp
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project


def post_sync_kind(title: str, run_tag: str, provider: str) -> str | None:
    """Classify the two exact run-owned mutation targets."""

    if not title.startswith(f"[{run_tag}:{provider}]"):
        return None
    if " · TUI · PostSync" in title:
        return "tui"
    if " · Markdown · PostSyncMD" in title:
        return "markdown"
    return None


def remote_stub(provider: str, identity: str, key: str, title: str):  # type: ignore[no-untyped-def]
    """Recreate only the provider identity metadata required by exact GET."""

    from pykantui.tracker.models import RemoteIssue  # noqa: PLC0415

    extra: dict[str, object] = {}
    if provider == "github" and "#" in key:
        extra["number"] = int(key.rsplit("#", 1)[1])
    return RemoteIssue(issue_id=identity, key=key, title=title, extra=extra)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _changed_local(workspace: Path, project: Project, run_tag: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        title = str(parsed.front.get("title", ""))
        kind = post_sync_kind(title, run_tag, project.provider)
        if kind is None:
            continue
        identity = str(parsed.front.get("id", ""))
        if identity.startswith("draft-") or not identity:
            raise RuntimeError(f"mutation target is not canonical: {path.name}")
        found[identity] = {
            "kind": kind,
            "key": str(parsed.front.get("key", "") or identity),
            "title": title,
            "body": parsed.source,
            "status": str(parsed.front.get("status", "")),
            "file": path.name,
            "comment_drafts": str(len(parsed.comment_drafts)),
            "comment_body": parsed.comment_drafts[0].body if len(parsed.comment_drafts) == 1 else "",
        }
    if len(found) != 2 or {item["kind"] for item in found.values()} != {"tui", "markdown"}:
        raise RuntimeError(f"expected two distinct post-sync targets, found {len(found)}")
    return found


async def _wait_for(app: KanbanApp, pilot: object, screen_type: type, timeout: float) -> object:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if isinstance(app.screen, screen_type):
            return app.screen
        await asyncio.sleep(0.1)
        await pilot.pause()  # type: ignore[attr-defined]
    raise TimeoutError(f"timed out waiting for {screen_type.__name__}")


async def execute(
    workspace: Path,
    artifacts: Path,
    *,
    run_tag: str,
    expected_project_id: str,
    armed: bool,
) -> Path:
    project = Project.load(workspace)
    context = CertificationContext(
        provider=project.provider,
        expected_project_id=expected_project_id,
        actual_project_id=project.project_id,
        run_tag=run_tag,
    )
    if not armed or os.environ.get("PYKANTUI_LIVE_WRITES") != "1":
        raise WritesDisabledError("live mutation sync requires --execute and PYKANTUI_LIVE_WRITES=1")
    target = artifacts / run_tag / "mutation-sync" / project.provider
    target.mkdir(parents=True, exist_ok=True)
    receipts = ReceiptLog(artifacts / run_tag / "receipts.jsonl", sensitive_values=list(project.secrets().values()))
    operation = "tui-sync-edit-move-comment"
    operation_id = "mutation-v1"
    if receipts.was_attempted(context, operation, operation_id):
        raise RuntimeError("this provider mutation batch already has an attempted receipt")

    before = _changed_local(workspace, project, run_tag)
    comment_bodies = [item["comment_body"] for item in before.values() if item["comment_body"]]
    if sum(int(item["comment_drafts"]) for item in before.values()) != 1 or len(comment_bodies) != 1:
        raise RuntimeError("expected exactly one local comment draft")
    comment_body = comment_bodies[0]

    provider = project.open()
    try:
        backend = ProviderBackend(workspace, provider, project.remote())
        plan = backend.plan_sync()
        if (
            plan.creates
            or len(plan.clean()) != 2
            or plan.conflicts()
            or plan.unchecked()
            or len(plan.comment_pushes) != 1
            or plan.invalid
            or any(push.previous.issue_id not in before for push in plan.clean())
            or plan.comment_pushes[0].draft.body != comment_body
        ):
            raise RuntimeError(
                "unsafe mutation plan "
                f"creates={len(plan.creates)} clean={len(plan.clean())} "
                f"conflicts={len(plan.conflicts())} unchecked={len(plan.unchecked())} "
                f"comments={len(plan.comment_pushes)} invalid={len(plan.invalid)}"
            )
        receipts.append(
            event="attempted",
            context=context,
            operation=operation,
            operation_id=operation_id,
            details={"updates": 2, "comments": 1, "tui": True},
        )

        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await _wait_for(app, pilot, SyncConfirmScreen, 120)
            app.save_screenshot(str((target / "01-preview.svg").resolve()))
            await pilot.click("#sync-send")
            progress = await _wait_for(app, pilot, SyncProgressScreen, 30)
            app.save_screenshot(str((target / "02-progress.svg").resolve()))
            close = progress.query_one("#sync-progress-close", Button)  # type: ignore[attr-defined]
            deadline = asyncio.get_running_loop().time() + 600
            captured = False
            while close.disabled and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.25)
                await pilot.pause()
                if not captured and " / " in str(progress.query_one("#sync-progress-fraction").render()):  # type: ignore[attr-defined]
                    app.save_screenshot(str((target / "03-progress-fraction.svg").resolve()))
                    captured = True
            if close.disabled:
                raise TimeoutError("mutation sync did not reach terminal UI state")
            app.save_screenshot(str((target / "04-result.svg").resolve()))
            phase = str(progress.query_one("#sync-progress-phase").render())  # type: ignore[attr-defined]
            summary = str(progress.query_one("#sync-progress-summary").render())  # type: ignore[attr-defined]
            unsafe_words = ("failed", "held", "skipped")
            if any(word in f"{phase} {summary}".casefold() for word in unsafe_words):
                raise RuntimeError(f"mutation sync did not fully succeed: {phase} {summary}")
            if "sent 2" not in summary or "commented 1" not in summary:
                raise RuntimeError(f"unexpected mutation result: {summary}")
            await pilot.click("#sync-progress-close")
            await pilot.pause()

        after = _changed_local(workspace, project, run_tag)
        if any(int(item["comment_drafts"]) for item in after.values()):
            raise RuntimeError("confirmed comment draft remains local")
        verified: list[dict[str, object]] = []
        comment_ids: list[str] = []
        for identity, local in after.items():
            exact = provider.get_issue(
                project.project_id,
                remote_stub(project.provider, identity, local["key"], local["title"]),
            )
            if exact is None:
                raise RuntimeError(f"exact read returned nothing for {identity}")
            if exact.title != local["title"] or exact.body.strip() != local["body"].strip():
                raise RuntimeError(f"direct field readback mismatch for {identity}")
            if local["kind"] == "tui" and local["status"] and exact.status != local["status"]:
                raise RuntimeError(f"direct status readback mismatch for {identity}")
            if local["kind"] == "tui":
                comments = provider.comments(project.project_id, exact, refresh=True)
                matches = [comment for comment in comments if comment.body == comment_body]
                if len(matches) != 1:
                    raise RuntimeError(f"direct comment readback found {len(matches)}, expected one")
                comment_ids = [comment.comment_id for comment in matches]
            verified.append(
                {
                    "remote_id": exact.issue_id,
                    "kind": local["kind"],
                    "title_sha256": _hash(exact.title),
                    "body_sha256": _hash(exact.body),
                    "status": exact.status,
                }
            )
        result = {
            "schema": 1,
            "provider": project.provider,
            "project_id": project.project_id,
            "run_tag": run_tag,
            "updates": 2,
            "moves": 1,
            "comments": 1,
            "direct_exact_reads": len(verified),
            "comment_ids": comment_ids,
            "cards": sorted(verified, key=lambda item: str(item["remote_id"])),
        }
        result_path = target / "mutation-api-readback.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipts.append(
            event="verified",
            context=context,
            operation=operation,
            operation_id=operation_id,
            details={"updates": 2, "moves": 1, "comments": 1, "direct_exact_reads": 2},
        )
        return result_path
    finally:
        provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--expected-project-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        execute(
            args.workspace.resolve(),
            args.artifacts.resolve(),
            run_tag=args.run_tag,
            expected_project_id=args.expected_project_id,
            armed=args.execute,
        )
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
