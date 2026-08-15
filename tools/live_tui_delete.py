"""Delete one unsent run-owned card per provider through the real TUI."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import OptionList

from pykantui.sync.provider import ProviderBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.workspace import layout
from pykantui.workspace.project import Project

PROVIDERS = ("asana", "clickup", "github", "jira", "linear", "monday", "plane", "shortcut", "trello")


async def _choose(pilot: Pilot[None], app: KanbanApp, label: str) -> None:
    options = app.screen.query_one(OptionList)
    index = next(
        position
        for position in range(options.option_count)
        if label in str(options.get_option_at_index(position).prompt)
    )
    options.highlighted = index
    await pilot.press("enter")


async def delete_one(workspace: Path, artifacts: Path, *, run_tag: str) -> dict[str, object]:
    project = Project.load(workspace)
    provider = project.open()
    try:
        backend = ProviderBackend(workspace, provider, project.remote())
        app = KanbanApp(backend, confirm_moves=False)
        target = artifacts / run_tag / "live-local" / project.provider
        target.mkdir(parents=True, exist_ok=True)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.view.card_filter.text = run_tag
            await app.action_refresh_board()
            await pilot.pause()
            card = next(
                item
                for item in app.query(TaskCard)
                if run_tag in item.task_.title and item.task_.title.endswith("card 20")
            )
            card.focus()
            before = len([task for task in backend.get_tasks() if run_tag in task.title])
            await pilot.press("d")
            await pilot.pause()
            app.save_screenshot(str((target / "delete-confirm.svg").resolve()))
            await _choose(pilot, app, "Delete cards")
            await app.workers.wait_for_complete()
            await pilot.pause()
            after = len([task for task in backend.get_tasks() if run_tag in task.title])
            if before != 20 or after != 19:
                raise RuntimeError(f"{project.provider} delete count was {before}->{after}, expected 20->19")
            app.save_screenshot(str((target / "delete-complete.svg").resolve()))
            trashed = list(layout.trash_dir(workspace).rglob("*.md"))
            if len(trashed) != 1:
                raise RuntimeError(f"{project.provider} expected one quarantined draft, got {len(trashed)}")
            return {
                "provider": project.provider,
                "project_id": project.project_id,
                "before": before,
                "after": after,
                "trash": trashed[0].name,
                "provider_writes": 0,
            }
    finally:
        provider.close()


async def run_all(workspace_root: Path, artifacts: Path, *, run_tag: str) -> Path:
    results = [await delete_one(workspace_root / name, artifacts, run_tag=run_tag) for name in PROVIDERS]
    target = artifacts / run_tag / "live-local" / "local-delete.json"
    target.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    args = parser.parse_args()
    print(asyncio.run(run_all(args.workspace_root.resolve(), args.artifacts.resolve(), run_tag=args.run_tag)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
