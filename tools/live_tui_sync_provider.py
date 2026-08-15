"""Run one armed provider Sync through the real Textual UI and verify API truth."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from live_provider_certification import CertificationContext, ReceiptLog, WritesDisabledError
from textual.widgets import Button

from pykantui.pages.sync import SyncConfirmScreen, SyncProgressScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tui.app import KanbanApp
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project


async def _wait_for(app: KanbanApp, pilot: object, screen_type: type, timeout: float) -> object:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if isinstance(app.screen, screen_type):
            return app.screen
        await asyncio.sleep(0.1)
        await pilot.pause()  # type: ignore[attr-defined]
    raise TimeoutError(f"timed out waiting for {screen_type.__name__}")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _local_owned(workspace: Path, project: Project, run_tag: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        title = str(parsed.front.get("title", ""))
        if run_tag not in title:
            continue
        issue_id = str(parsed.front.get("id", ""))
        found[issue_id] = {
            "title": title,
            "title_sha256": _hash(title),
            "body_sha256": _hash(parsed.source),
            "file": path.name,
        }
    return found


async def execute(
    workspace: Path,
    artifacts: Path,
    *,
    run_tag: str,
    expected_project_id: str,
    armed: bool,
    operation_id: str = "create-19-v1",
) -> Path:
    project = Project.load(workspace)
    context = CertificationContext(
        provider=project.provider,
        expected_project_id=expected_project_id,
        actual_project_id=project.project_id,
        run_tag=run_tag,
    )
    if not armed or os.environ.get("PYKANTUI_LIVE_WRITES") != "1":
        raise WritesDisabledError("live sync requires --execute and PYKANTUI_LIVE_WRITES=1")
    target = artifacts / run_tag / "live-sync" / project.provider
    target.mkdir(parents=True, exist_ok=True)
    receipts = ReceiptLog(artifacts / run_tag / "receipts.jsonl", sensitive_values=project.secrets().values())
    operation = "tui-sync-create-batch"
    if receipts.was_attempted(context, operation, operation_id):
        raise RuntimeError("this provider create batch already has an attempted receipt")

    provider = project.open()
    try:
        backend = ProviderBackend(workspace, provider, project.remote())
        plan = backend.plan_sync()
        marker = f"[{run_tag}:{project.provider}]"
        if (
            len(plan.creates) != 19
            or plan.pushes
            or plan.comment_pushes
            or plan.invalid
            or any(not title.startswith(marker) for title in plan.creates)
        ):
            raise RuntimeError(
                f"unsafe plan creates={len(plan.creates)} pushes={len(plan.pushes)} "
                f"comments={len(plan.comment_pushes)} invalid={len(plan.invalid)}"
            )
        receipts.append(
            event="attempted",
            context=context,
            operation=operation,
            operation_id=operation_id,
            details={"creates": 19, "tui": True},
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
            captured_fraction = False
            while close.disabled and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.25)
                await pilot.pause()
                if not captured_fraction and " / " in str(progress.query_one("#sync-progress-fraction").render()):  # type: ignore[attr-defined]
                    app.save_screenshot(str((target / "03-progress-fraction.svg").resolve()))
                    captured_fraction = True
            if close.disabled:
                raise TimeoutError("live provider sync did not reach a terminal UI state")
            app.save_screenshot(str((target / "04-result.svg").resolve()))
            summary = str(progress.query_one("#sync-progress-summary").render())  # type: ignore[attr-defined]
            phase = str(progress.query_one("#sync-progress-phase").render())  # type: ignore[attr-defined]
            unsafe_words = ("failed", "held", "skipped")
            if any(word in f"{phase} {summary}".casefold() for word in unsafe_words):
                raise RuntimeError(f"provider sync did not fully succeed: {phase} {summary}")
            await pilot.click("#sync-progress-close")
            await pilot.pause()

        local = _local_owned(workspace, project, run_tag)
        if len(local) != 19 or any(identity.startswith("draft-") for identity in local):
            raise RuntimeError(f"local canonical card count is {len(local)}, expected 19")

        provider.refresh()
        remote_list = [item for item in provider.iter_issues(project.project_id) if run_tag in item.title]
        if len(remote_list) != 19:
            raise RuntimeError(f"direct provider list returned {len(remote_list)} run cards, expected 19")
        verified = []
        for item in remote_list:
            exact = provider.get_issue(project.project_id, item)
            if exact is None or exact.issue_id not in local or exact.title != local[exact.issue_id]["title"]:
                raise RuntimeError(f"direct provider readback mismatch for {item.display_key()}")
            verified.append(
                {
                    "remote_id": exact.issue_id,
                    "key": exact.display_key(),
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
            "created": 19,
            "direct_exact_reads": len(verified),
            "cards": sorted(verified, key=lambda item: str(item["remote_id"])),
        }
        result_path = target / "create-api-readback.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipts.append(
            event="verified",
            context=context,
            operation=operation,
            operation_id=operation_id,
            details={"creates": 19, "direct_exact_reads": len(verified)},
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
    parser.add_argument("--operation-id", default="create-19-v1")
    args = parser.parse_args()
    result = asyncio.run(
        execute(
            args.workspace.resolve(),
            args.artifacts.resolve(),
            run_tag=args.run_tag,
            expected_project_id=args.expected_project_id,
            armed=args.execute,
            operation_id=args.operation_id,
        )
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
