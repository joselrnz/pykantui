"""Prove an aligned live workspace performs a screenshot-backed no-op Sync."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from textual.widgets import Button

from pykantui.pages.sync import SyncProgressScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tui.app import KanbanApp
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project


def terminal_is_safe(phase: str, summary: str) -> bool:
    """Return whether a terminal Sync state reports no failure/held work."""

    text = f"{phase} {summary}".casefold()
    return not any(word in text for word in ("failed", "held", "skipped", "stopped"))


def _owned_hashes(workspace: Path, project: Project, run_tag: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        if run_tag not in str(parsed.front.get("title", "")):
            continue
        identity = str(parsed.front.get("id", ""))
        if identity.startswith("draft-"):
            raise RuntimeError(f"active draft remains before no-op: {path.name}")
        hashes[identity] = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(hashes) != 19:
        raise RuntimeError(f"expected 19 tagged canonical files, found {len(hashes)}")
    return hashes


async def execute(workspace: Path, artifacts: Path, *, run_tag: str) -> Path:
    project = Project.load(workspace)
    provider = project.open()
    target = artifacts / run_tag / "noop-sync" / project.provider
    target.mkdir(parents=True, exist_ok=True)
    try:
        backend = ProviderBackend(workspace, provider, project.remote())
        before_plan = backend.plan_sync()
        if not before_plan.is_empty():
            raise RuntimeError(f"pre-noop plan is not empty: {before_plan.describe()}")
        before_hashes = _owned_hashes(workspace, project, run_tag)

        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            deadline = asyncio.get_running_loop().time() + 600
            preparing_saved = False
            terminal: SyncProgressScreen | None = None
            while asyncio.get_running_loop().time() < deadline:
                if isinstance(app.screen, SyncProgressScreen):
                    screen = app.screen
                    if not preparing_saved:
                        app.save_screenshot(str((target / "01-progress.svg").resolve()))
                        preparing_saved = True
                    close = screen.query_one("#sync-progress-close", Button)
                    if not close.disabled:
                        terminal = screen
                        break
                await asyncio.sleep(0.1)
                await pilot.pause()
            if terminal is None:
                raise TimeoutError("no-op Sync did not reach terminal state")
            app.save_screenshot(str((target / "02-result.svg").resolve()))
            phase = str(terminal.query_one("#sync-progress-phase").render())
            summary = str(terminal.query_one("#sync-progress-summary").render())
            if not terminal_is_safe(phase, summary):
                raise RuntimeError(f"no-op Sync terminal is unsafe: {phase} {summary}")
            await pilot.click("#sync-progress-close")
            await pilot.pause()

        after_hashes = _owned_hashes(workspace, project, run_tag)
        if before_hashes != after_hashes:
            changed = sorted(set(before_hashes) | set(after_hashes))
            changed = [identity for identity in changed if before_hashes.get(identity) != after_hashes.get(identity)]
            raise RuntimeError(f"no-op Sync changed {len(changed)} tagged Markdown files")
        after_plan = backend.plan_sync()
        if not after_plan.is_empty():
            raise RuntimeError(f"post-noop plan is not empty: {after_plan.describe()}")
        provider.refresh()
        remote = [item for item in provider.iter_issues(project.project_id) if run_tag in item.title]
        if len(remote) != 19:
            raise RuntimeError(f"direct provider list found {len(remote)} tagged cards")
        result = {
            "schema": 1,
            "provider": project.provider,
            "project_id": project.project_id,
            "run_tag": run_tag,
            "before_plan": "empty",
            "after_plan": "empty",
            "tagged_markdown_files": len(after_hashes),
            "tagged_markdown_bytes_stable": True,
            "direct_remote_count": len(remote),
            "provider_mutations": 0,
            "terminal_phase": phase,
            "terminal_summary": summary,
        }
        output = target / "noop-verification.json"
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return output
    finally:
        provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    args = parser.parse_args()
    print(
        asyncio.run(
            execute(
                args.workspace.resolve(),
                args.artifacts.resolve(),
                run_tag=args.run_tag,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
