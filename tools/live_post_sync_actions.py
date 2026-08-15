"""Stage post-create TUI, Markdown, move, and comment changes without Sync."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from textual.widgets import Input, Select, TabbedContent, TextArea

from pykantui.models import BoardLayout
from pykantui.sync.provider import ProviderBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project


def select_next_column(columns: tuple[str, ...], current: str) -> str:
    """Choose a deterministic different column, wrapping at the end."""

    if len(columns) < 2:
        return current or (columns[0] if columns else "")
    try:
        index = columns.index(current)
    except ValueError:
        return columns[0]
    return columns[(index + 1) % len(columns)]


def raw_post_sync_markdown_edit(
    path: Path,
    *,
    run_tag: str,
    provider: str,
    edit_body: bool = True,
) -> None:
    """Make one owned raw Markdown title/body edit while preserving managed regions."""

    text = path.read_text(encoding="utf-8")
    title_lines = [line for line in text.splitlines() if line.startswith("title:")]
    if len(title_lines) != 1 or run_tag not in title_lines[0] or f":{provider}]" not in title_lines[0]:
        raise RuntimeError(f"refusing unowned Markdown edit: {path.name}")
    title_line = title_lines[0]
    if " · PostSyncMD" not in title_line:
        replacement = title_line[:-1] + " · PostSyncMD'" if title_line.endswith("'") else title_line + " · PostSyncMD"
        text = text.replace(title_line, replacement, 1)
    body_line = f"Markdown post-sync edit for {run_tag}."
    if not edit_body and body_line in text:
        text = text.replace(f"\n\n{body_line}\n\n", "\n\n", 1)
        text = text.replace(f"\n{body_line}\n", "\n", 1)
    elif edit_body and body_line not in text:
        marker = "<!-- pykantui:"
        marker_index = text.find(marker)
        if marker_index < 0:
            text = text.rstrip() + f"\n\n{body_line}\n"
        else:
            prefix = text[:marker_index].rstrip()
            suffix = text[marker_index:]
            text = f"{prefix}\n\n{body_line}\n\n{suffix}"
    path.write_text(text, encoding="utf-8")
    parsed = markdown.read(path)
    if not parsed.valid or run_tag not in str(parsed.front.get("title", "")):
        raise RuntimeError(f"post-sync Markdown edit did not validate: {path.name}")


def _owned_path(workspace: Path, project: Project, *, run_tag: str, title_fragment: str) -> Path:
    matches = []
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        identity = str(parsed.front.get("id", ""))
        title = str(parsed.front.get("title", ""))
        if run_tag in title and title_fragment in title and not identity.startswith("draft-"):
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one canonical {title_fragment} Markdown file, found {len(matches)}"
        )
    return matches[0]


async def exercise(workspace: Path, artifact_root: Path, *, run_tag: str) -> dict[str, object]:
    project = Project.load(workspace)
    provider = project.open()
    target = artifact_root / run_tag / "post-create" / project.provider
    target.mkdir(parents=True, exist_ok=True)
    try:
        backend = ProviderBackend(workspace, provider, project.remote())
        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.view.card_filter.text = run_tag
            await app.action_refresh_board()
            await pilot.pause()
            owned = [task for task in backend.get_tasks() if run_tag in task.title]
            if len(owned) != 19:
                raise RuntimeError(f"{project.provider} expected 19 canonical cards, found {len(owned)}")

            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)
            edited = next(
                (task for task in owned if " · TUI" in task.title and " · PostSync" not in task.title),
                None,
            )
            if edited is None:
                edited = next((task for task in owned if " · TUI · PostSync" in task.title), None)
            if edited is None:
                raise RuntimeError("canonical TUI-edited target is missing")
            view._select(str(edited.task_id))
            await pilot.pause()
            current_column = str(edited.column_id)
            columns = tuple(str(column.column_id) for column in app.visible_columns)
            next_column = select_next_column(columns, current_column)

            await pilot.press("e")
            await pilot.pause()
            summary = app.query_one("#work-item-edit-summary", Input)
            if " · PostSync" not in summary.value:
                summary.value += " · PostSync"
            description = app.query_one("#work-item-edit-description", TextArea)
            body_line = f"Post-create TUI edit for {run_tag}."
            if body_line not in description.text:
                description.load_text(description.text.rstrip() + f"\n\n{body_line}")
            status = app.query("#work-item-edit-status")
            if status and not status.first(Select).disabled and next_column:
                status.first(Select).value = next_column
            await pilot.press("ctrl+s")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=30)
            backend.reload_local()
            await app.action_refresh_board()
            await pilot.pause()

            refreshed = next(
                (
                    task
                    for task in backend.get_tasks()
                    if run_tag in task.title and " · TUI · PostSync" in task.title
                ),
                None,
            )
            if refreshed is None or " · PostSync" not in refreshed.title:
                raise RuntimeError("post-create TUI edit did not persist")
            if len(columns) > 1 and str(refreshed.column_id) != next_column:
                raise RuntimeError("post-create TUI move did not persist")
            view._select(str(refreshed.task_id))
            await pilot.pause()
            edit_svg = target / "01-tui-edit-move.svg"
            app.save_screenshot(str(edit_svg.resolve()))

            tabs = view.query_one("#work-item-tabs", TabbedContent)
            tabs.active = "work-item-comments-tab"
            await pilot.pause()
            current_path = _owned_path(
                workspace,
                project,
                run_tag=run_tag,
                title_fragment=" · TUI · PostSync",
            )
            existing_drafts = markdown.read(current_path).comment_drafts
            if len(existing_drafts) > 1:
                raise RuntimeError("canonical card has more than one pending comment")
            if existing_drafts:
                comment = existing_drafts[0].body
            else:
                composer = view.query_one("#work-item-comment-draft", TextArea)
                if composer.disabled:
                    raise RuntimeError("canonical card comment composer is unexpectedly disabled")
                comment = f"[{run_tag}:{project.provider}] post-create comment from TUI"
                composer.load_text(comment)
                await pilot.click("#work-item-comment-add-local")
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=30)
                await pilot.pause()
            comment_svg = target / "02-comment-draft.svg"
            app.save_screenshot(str(comment_svg.resolve()))

            second = _owned_path(workspace, project, run_tag=run_tag, title_fragment=" · Markdown")
            raw_post_sync_markdown_edit(
                second,
                run_tag=run_tag,
                provider=project.provider,
                edit_body="body" in provider.editable_card_fields(),
            )
            backend.reload_local()
            await app.action_refresh_board()
            await pilot.pause()
            markdown_svg = target / "03-markdown-edit.svg"
            app.save_screenshot(str(markdown_svg.resolve()))

        card_one = _owned_path(
            workspace,
            project,
            run_tag=run_tag,
            title_fragment=" · TUI · PostSync",
        )
        parsed_one = markdown.read(card_one)
        if len(parsed_one.comment_drafts) != 1 or parsed_one.comment_drafts[0].body != comment:
            raise RuntimeError("local comment draft did not round-trip through Markdown")
        return {
            "provider": project.provider,
            "project_id": project.project_id,
            "card_one": card_one.name,
            "card_two": second.name,
            "from_column": current_column,
            "to_column": next_column,
            "comment_drafts": len(parsed_one.comment_drafts),
            "provider_writes": 0,
            "screenshots": [edit_svg.name, comment_svg.name, markdown_svg.name],
        }
    finally:
        provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    args = parser.parse_args()
    result = asyncio.run(
        exercise(
            args.workspace.resolve(),
            args.artifacts.resolve(),
            run_tag=args.run_tag,
        )
    )
    output = args.artifacts / args.run_tag / "post-create" / str(result["provider"]) / "actions.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
