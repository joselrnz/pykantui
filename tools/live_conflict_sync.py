"""Create and resolve one run-owned title conflict through the real TUI."""

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
    except ModuleNotFoundError:
        from tools.live_provider_certification import CertificationContext, ReceiptLog, WritesDisabledError
from textual.widgets import Button

from pykantui.pages.sync import SyncConfirmScreen, SyncProgressScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.models import IssueEdit, RemoteIssue
from pykantui.tui.app import KanbanApp
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project


def conflict_titles(original: str, *, run_tag: str, provider: str) -> tuple[str, str]:
    """Return deterministic divergent titles for one exact owned card."""

    if not original.startswith(f"[{run_tag}:{provider}]"):
        raise RuntimeError("refusing conflict edit for unowned title")
    base = original.removesuffix(" · RemoteConflict").removesuffix(" · LocalConflict")
    return base + " · RemoteConflict", base + " · LocalConflict"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _local_path(workspace: Path, project: Project, identity: str) -> Path:
    matches = []
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        if str(parsed.front.get("id", "")) == identity:
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError(f"expected one local file for {identity}, found {len(matches)}")
    return matches[0]


def _write_local_title(path: Path, *, expected: str, wanted: str) -> None:
    text = path.read_text(encoding="utf-8")
    title_lines = [line for line in text.splitlines() if line.startswith("title:")]
    if len(title_lines) != 1 or expected not in title_lines[0]:
        raise RuntimeError(f"local title changed before conflict staging: {path.name}")
    old = title_lines[0]
    new = old.replace(expected, wanted, 1)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    parsed = markdown.read(path)
    if not parsed.valid or str(parsed.front.get("title", "")) != wanted:
        raise RuntimeError(f"local conflict title is invalid: {path.name}")


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
        raise WritesDisabledError("live conflict requires --execute and PYKANTUI_LIVE_WRITES=1")
    target = artifacts / run_tag / "conflict-sync" / project.provider
    target.mkdir(parents=True, exist_ok=True)
    receipts = ReceiptLog(artifacts / run_tag / "receipts.jsonl", sensitive_values=list(project.secrets().values()))
    operation = "direct-remote-conflict-edit"
    operation_id = "title-conflict-v1"
    if receipts.was_attempted(context, operation, operation_id):
        raise RuntimeError("this provider conflict edit already has an attempted receipt")

    provider = project.open()
    try:
        provider.refresh()
        candidates = [
            issue
            for issue in provider.iter_issues(project.project_id)
            if issue.title.startswith(f"[{run_tag}:{project.provider}]")
            and "PostSync" not in issue.title
            and "Conflict" not in issue.title
        ]
        if not candidates:
            raise RuntimeError("no untouched run-owned conflict candidate")
        issue = sorted(candidates, key=lambda item: item.display_key())[0]
        local_path = _local_path(workspace, project, issue.issue_id)
        local_before = markdown.read(local_path)
        original = str(local_before.front.get("title", ""))
        if original != issue.title:
            raise RuntimeError("local/provider title mismatch before conflict staging")
        remote_title, local_title = conflict_titles(original, run_tag=run_tag, provider=project.provider)

        receipts.append(
            event="attempted",
            context=context,
            operation=operation,
            operation_id=operation_id,
            details={"remote_id_sha256": _hash(issue.issue_id), "field": "title"},
        )
        try:
            provider.update_issue(issue, IssueEdit(title=remote_title))
        except Exception:
            receipts.append(
                event="ambiguous",
                context=context,
                operation=operation,
                operation_id=operation_id,
                details={"remote_id_sha256": _hash(issue.issue_id)},
            )
            raise
        remote = provider.get_issue(project.project_id, issue)
        if remote is None or remote.title != remote_title:
            receipts.append(
                event="ambiguous",
                context=context,
                operation=operation,
                operation_id=operation_id,
                details={"remote_id_sha256": _hash(issue.issue_id), "readback": "mismatch"},
            )
            raise RuntimeError("remote conflict edit did not verify")
        receipts.append(
            event="verified",
            context=context,
            operation=operation,
            operation_id=operation_id,
            details={"remote_id_sha256": _hash(issue.issue_id), "field": "title"},
        )

        _write_local_title(local_path, expected=original, wanted=local_title)
        backend = ProviderBackend(workspace, provider, project.remote())
        plan = backend.plan_sync()
        if len(plan.conflicts()) != 1 or plan.clean() or plan.creates or plan.comment_pushes or plan.invalid:
            raise RuntimeError(
                "unexpected conflict plan "
                f"conflicts={len(plan.conflicts())} clean={len(plan.clean())} "
                f"creates={len(plan.creates)} comments={len(plan.comment_pushes)} invalid={len(plan.invalid)}"
            )
        conflict = plan.conflicts()[0]
        if conflict.previous.issue_id != issue.issue_id or conflict.conflicting_fields() != ("title",):
            raise RuntimeError("title conflict identity/field mismatch")

        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await _wait_for(app, pilot, SyncConfirmScreen, 120)
            app.save_screenshot(str((target / "01-conflict-preview.svg").resolve()))
            await pilot.click("#sync-use-provider")
            progress = await _wait_for(app, pilot, SyncProgressScreen, 30)
            app.save_screenshot(str((target / "02-progress.svg").resolve()))
            close = progress.query_one("#sync-progress-close", Button)  # type: ignore[attr-defined]
            deadline = asyncio.get_running_loop().time() + 600
            while close.disabled and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.25)
                await pilot.pause()
            if close.disabled:
                raise TimeoutError("conflict resolution did not reach terminal state")
            app.save_screenshot(str((target / "03-result.svg").resolve()))
            phase = str(progress.query_one("#sync-progress-phase").render())  # type: ignore[attr-defined]
            summary = str(progress.query_one("#sync-progress-summary").render())  # type: ignore[attr-defined]
            if "failed" in f"{phase} {summary}".casefold() or "accepted provider version 1" not in summary:
                raise RuntimeError(f"conflict resolution failed: {phase} {summary}")
            await pilot.click("#sync-progress-close")
            await pilot.pause()

        final_local = markdown.read(_local_path(workspace, project, issue.issue_id))
        final_remote = provider.get_issue(
            project.project_id,
            RemoteIssue(
                issue_id=issue.issue_id,
                key=issue.key,
                title=remote_title,
                extra=issue.extra,
            ),
        )
        if str(final_local.front.get("title", "")) != remote_title:
            raise RuntimeError("local conflict did not accept provider title")
        if final_remote is None or final_remote.title != remote_title:
            raise RuntimeError("provider title changed during provider-version resolution")
        result = {
            "schema": 1,
            "provider": project.provider,
            "project_id": project.project_id,
            "run_tag": run_tag,
            "remote_id_sha256": _hash(issue.issue_id),
            "field": "title",
            "resolution": "provider",
            "local_title_sha256": _hash(remote_title),
            "remote_title_sha256": _hash(final_remote.title),
            "aligned": True,
        }
        result_path = target / "conflict-api-readback.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
