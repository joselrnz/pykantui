"""Drive realistic local action flows for each isolated live-provider workspace.

No sync writes are performed in this script.  It captures a full action timeline,
writes per-step PNGs, stores a manifest, and can build one GIF per provider.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from PIL import Image
from textual.dom import DOMNode
from textual.widget import Widget
from textual.widgets import Input, Select, TabbedContent, TextArea

from pykantui.commands.new import write_draft
from pykantui.models import BoardLayout, Task
from pykantui.pages.sync import SyncConfirmScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.base import Provider
from pykantui.tracker.models import IssueDraft
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project

PROVIDERS = ("asana", "clickup", "github", "jira", "linear", "monday", "plane", "shortcut", "trello")

# Keep every frame visibly long enough for review in docs/gifs.
ACTION_STEPS = (
    "00-filtered-split",
    "01-filtered-rows",
    "02-open-popup",
    "03-popup-closed",
    "04-edit-open",
    "05-edit-saved",
    "06-edit-closed",
    "07-comments-tab",
    "08-comment-draft",
    "09-tab-info",
    "09b-card-cycle-open",
    "09c-card-cycle-close",
    "10-raw-markdown-refresh",
    "11-sync-preview",
    "12-sync-open",
    "13-sync-mode-action",
    "14-sync-complete",
    "15-final-board",
)

HOLD_SECONDS: dict[str, float] = {
    "00-filtered-split": 5.8,
    "01-filtered-rows": 5.1,
    "02-open-popup": 5.7,
    "03-popup-closed": 4.0,
    "04-edit-open": 5.4,
    "05-edit-saved": 5.1,
    "06-edit-closed": 4.5,
    "07-comments-tab": 4.8,
    "08-comment-draft": 4.8,
    "09-tab-info": 4.2,
    "09b-card-cycle-open": 4.4,
    "09c-card-cycle-close": 4.4,
    "10-raw-markdown-refresh": 4.8,
    "11-sync-preview": 4.2,
    "12-sync-open": 3.6,
    "12-sync-pull": 3.9,
    "13-sync-mode-action": 3.6,
    "14-sync-complete": 4.2,
    "15-final-board": 4.8,
}


def _slugify(value: str) -> str:
    lowered = (value or "").strip().lower().replace("\ufeff", "")
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered, flags=re.UNICODE)
    lowered = lowered.strip("-")
    return lowered or "provider"

SAVE_RETRY_COUNT = 12

ENTERPRISE_EDIT_PREFIX = {
    "asana": "Identity Platform Program",
    "clickup": "Cloud Migration Workstream",
    "github": "Reliability Platform Expansion",
    "jira": "Enterprise Delivery Pipeline",
    "linear": "Roadmap Enablement Program",
    "monday": "Program Operations Board",
    "plane": "Tenant Operations Upgrade",
    "shortcut": "Incident Response Readiness",
    "trello": "Cross-Team Planning Engine",
}

MIN_OWNER_CARDS = 24


def _choose_first_column_name(project: Project, provider: Provider) -> str:
    columns = provider.columns(project.project_id)
    if not columns:
        raise RuntimeError(f"{project.provider} has no columns for local draft seeding")
    return columns[0].name


def _ensure_minimum_owned_cards(
    workspace: Path,
    project: Project,
    provider: Provider,
    *,
    min_count: int = MIN_OWNER_CARDS,
    run_tag: str,
) -> None:
    """Ensure a consistent owned local backlog for this provider before capture."""

    existing = len(_owned_paths(workspace, project, run_tag))
    if existing >= min_count:
        return

    columns = provider.columns(project.project_id)
    target_column = columns[0]
    enterprise_project = project.name or project.key or project.project_id
    owner_prefix = ENTERPRISE_EDIT_PREFIX.get(project.provider, "Enterprise Workstream")
    provider_marker = f"[{run_tag}:{project.provider}]"

    issue_type_name = ""
    if hasattr(provider, "resolve_issue_type"):
        try:
            issue_type = provider.resolve_issue_type(project.project_id, "")
            if issue_type is not None:
                issue_type_name = issue_type.name
        except Exception:
            issue_type_name = ""

    for index in range(existing + 1, min_count + 1):
        draft = IssueDraft(
            title=(
                f"{provider_marker} {enterprise_project} · {owner_prefix} Work Item "
                f"{index:02d} · {project.project_id}"
            ),
            body=(
                f"Delivery runbook proof card for provider {project.provider}.\n"
                f"Run tag: {run_tag}\n"
                f"Project: {enterprise_project} ({project.project_id})"
            ),
            column_id=target_column.column_id,
            issue_type=issue_type_name,
            assignee="",
            labels=(project.provider, run_tag),
            components=(),
            priority="",
        )
        write_draft(workspace, project, target_column, draft)


@dataclass(frozen=True)
class TimelineFrame:
    stem: str
    purpose: str
    hold_seconds: float


def _owned_paths(workspace: Path, project: Project, run_tag: str) -> list[Path]:
    marker = f"[{run_tag}:{project.provider}] card "
    enterprise_slug = _slugify(ENTERPRISE_EDIT_PREFIX.get(project.provider, project.provider))
    found = []
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        front_labels = parsed.front.get("labels", [])
        label_owner = isinstance(front_labels, (list, tuple)) and any(
            str(value) == run_tag for value in front_labels
        )
        title = str(parsed.front.get("title", "")).lower()
        filename = path.name.lower()
        looks_local_flow = "work-item" in title and enterprise_slug in filename
        if (
            str(parsed.front.get("title", "")).startswith(marker)
            or label_owner
            or run_tag in str(parsed.front.get("title", ""))
            or run_tag in str(parsed.source)
            or looks_local_flow
        ):
            found.append(path)
    return sorted(found)


def _owned_tasks_from_backend(backend: ProviderBackend, provider: str, run_tag: str) -> list[Task]:
    marker = f"[{run_tag}:{provider}] card "
    slug = _slugify(ENTERPRISE_EDIT_PREFIX.get(provider, provider))

    def is_owned(task: Task) -> bool:
        if marker in task.title or f"{run_tag}" in task.title:
            return True
        metadata = getattr(task, "metadata", {})
        labels = metadata.get("labels", ())
        if isinstance(labels, (list, tuple)) and run_tag in labels:
            return True
        description = getattr(task, "description", "") or ""
        title = str(task.title).lower()
        return run_tag in description or ("work item" in title and slug in title.lower())

    tasks = [task for task in backend.get_tasks() if is_owned(task)]
    if not tasks:
        marker_prefix = f"[{provider}] card "
        tasks = [task for task in backend.get_tasks() if marker_prefix in task.title]
    return sorted(tasks, key=lambda task: task.title)


def _find_task_by_id(
    backend: ProviderBackend,
    provider: str,
    task_id: str,
    run_tag: str,
    *,
    backend_reload: bool = True,
) -> Task | None:
    if backend_reload:
        with contextlib.suppress(Exception):
            backend.reload_local()
    for task in _owned_tasks_from_backend(backend, provider, run_tag):
        if str(task.task_id) == task_id:
            return task
    return None


async def _wait_for_task_id(
    backend: ProviderBackend,
    provider: str,
    task_id: str,
    run_tag: str,
    *,
    delay: float = 0.25,
    retries: int = SAVE_RETRY_COUNT,
) -> Task | None:
    for _ in range(max(1, retries)):
        found = _find_task_by_id(backend, provider, task_id, run_tag, backend_reload=True)
        if found is not None:
            return found
        await asyncio.sleep(delay)
    return None


def _move_target_column_id(view: WorkItemsView, task_id: str, current_column_id: int | str) -> int | str | None:
    visible_ids = [c.column_id for c in view.app.visible_columns]
    if not visible_ids:
        return None
    if len(visible_ids) == 1:
        return visible_ids[0]
    for candidate in visible_ids:
        if str(candidate) != str(current_column_id):
            return candidate
    return visible_ids[0]


def raw_markdown_edit(path: Path, *, run_tag: str, provider: str) -> None:
    """Edit one owned title line without touching any other markdown region."""

    text = path.read_text(encoding="utf-8")
    parsed = markdown.read(path)
    marker = f"[{run_tag}:{provider}] card "
    title = str(parsed.front.get("title", ""))
    labels = parsed.front.get("labels", ())
    label_owner = isinstance(labels, (list, tuple)) and (
        run_tag in map(str, labels)
    )
    description_owner = run_tag in str(parsed.source)
    fallback = True
    owned = (
        title.startswith(marker)
        or run_tag in title
        or f":{provider}]" in title
        or label_owner
        or description_owner
    )
    owned = owned or (
        "work item" in title.lower()
        and _slugify(provider + " " + ENTERPRISE_EDIT_PREFIX.get(provider, "")) in path.name.lower()
    )
    if not owned:
        # Legacy or older-run naming still uses the legacy work-item workflow format
        # (slugified provider prefix + work-item + ids) but does not include run tags.
        legacy_prefix = _slugify(ENTERPRISE_EDIT_PREFIX.get(provider, provider))
        if "work-item" in title.lower() and legacy_prefix in path.name.lower():
            owned = True
            fallback = False
        elif "work-item" in path.name.lower():
            # Any locally generated work item template still keeps its own draft id.
            owned = path.stem.startswith("draft-")
    if not owned:
        raise RuntimeError(f"refusing unowned Markdown edit: {path.name}")

    title_lines = [line for line in text.splitlines() if line.startswith("title:")]
    if len(title_lines) != 1:
        raise RuntimeError(f"refusing malformed markdown title line: {path.name}")
    if " · Markdown" in title_lines[0]:
        return
    replacement = (
        title_lines[0][:-1] + " · Markdown'"
        if title_lines[0].endswith("'")
        else title_lines[0] + " · Markdown"
    )
    updated = text.replace(title_lines[0], replacement, 1)
    path.write_text(updated, encoding="utf-8")
    parsed = markdown.read(path)
    title = str(parsed.front.get("title", ""))
    labels = parsed.front.get("labels", ())
    label_owner = isinstance(labels, (list, tuple)) and run_tag in map(str, labels)
    body_owner = run_tag in str(parsed.source)
    if not parsed.valid or (run_tag not in title and not body_owner and not label_owner):
        raise RuntimeError(f"raw Markdown edit did not validate: {path.name}")
    if fallback:
        path.write_text(text.replace(title_lines[0], replacement, 1), encoding="utf-8")
        return


def _save_frame(app: KanbanApp, target: Path, stem: str) -> Path:
    path = target / f"{stem}.svg"
    app.save_screenshot(str(path.resolve()))
    # App backends may emit SVG when asked for PNG; normalize in frame reader.
    return path


def _append_frame(
    frame_specs: list[TimelineFrame],
    stem: str,
    purpose: str,
    *,
    hold_scale: float,
) -> TimelineFrame:
    frame = TimelineFrame(
        stem=stem,
        purpose=purpose,
        hold_seconds=HOLD_SECONDS[stem] * max(1.0, hold_scale),
    )
    frame_specs.append(frame)
    return frame


_WidgetT = TypeVar("_WidgetT", bound=Widget)


def _query_widget(node: DOMNode, selector: str, *, fallback: Widget | None = None) -> Widget | None:
    nodes = list(node.query(selector))
    if not nodes:
        return fallback
    return nodes[0]


def _query_typed(
    node: DOMNode,
    selector: str,
    widget_type: type[_WidgetT],
    *,
    fallback: _WidgetT | None = None,
) -> _WidgetT | None:
    for candidate in list(node.query(selector)):
        if isinstance(candidate, widget_type):
            return candidate
    return fallback


def _safe_text(node: object | None, fallback: str = "") -> str:
    return str(getattr(node, "value", fallback)) if node is not None else fallback


def _save_if_present(app: KanbanApp, target: Path, stem: str) -> None:
    with contextlib.suppress(Exception):
        _save_frame(app, target, stem)


async def _wait_for_screen(app: KanbanApp, *, screen_type: type, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if isinstance(app.screen, screen_type):
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"timed out waiting for screen: {screen_type}")


def _enterprise_summary(provider: str, run_tag: str, action: str) -> str:
    return f"{ENTERPRISE_EDIT_PREFIX.get(provider, 'Enterprise')} · {run_tag} · {action}"


async def exercise(
    workspace: Path,
    artifact_root: Path,
    *,
    run_tag: str,
    sync_mode: str,
    hold_scale: float,
) -> dict[str, object]:
    project = Project.load(workspace)
    provider = project.open()
    actions: list[dict[str, object]] = []
    frame_specs: list[TimelineFrame] = []
    target = artifact_root / run_tag / "live-local" / project.provider
    _ensure_minimum_owned_cards(workspace, project, provider, run_tag=run_tag, min_count=MIN_OWNER_CARDS)

    try:
        backend = ProviderBackend(workspace, provider, project.remote())
        app = KanbanApp(backend, confirm_moves=False)
        target.mkdir(parents=True, exist_ok=True)
        action_frames = target / "actions"
        action_frames.mkdir(parents=True, exist_ok=True)

        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause()
            app.view.card_filter.text = run_tag
            await app.action_refresh_board()
            await pilot.pause()
            if len(backend.get_tasks()) < 20:
                raise RuntimeError(f"{project.provider} has fewer than 20 local cards")

            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            view = app.query_one(WorkItemsView)

            owned_tasks = _owned_tasks_from_backend(backend, project.provider, run_tag)
            if not owned_tasks:
                raise RuntimeError(f"{project.provider} has no cards for run tag {run_tag}")
            edited = owned_tasks[0]
            tracked_task_id = str(edited.task_id)
            view._select(str(edited.task_id))
            await pilot.pause()

            target_column = _move_target_column_id(view, str(edited.task_id), edited.column_id)
            if target_column is None:
                raise RuntimeError(f"{project.provider} move target column unavailable")
            move_requested = str(target_column) != str(edited.column_id)

            await pilot.press("e")
            await pilot.pause()
            summary = _query_typed(app, "#work-item-edit-summary", Input)
            if summary is not None:
                summary.value = _enterprise_summary(project.provider, run_tag, "Ops")
            status = _query_typed(app, "#work-item-edit-status", Select)
            if status is not None and not status.disabled:
                status.value = str(target_column)
            description = _query_typed(app, "#work-item-edit-description", TextArea)
            if description and not description.disabled:
                description.load_text(
                    _safe_text(description, "")
                    + f"\nExecuted enterprise rollout run {run_tag} against {project.project_id}."
                )
            notes = _query_typed(app, "#work-item-edit-private-notes", TextArea)
            if notes:
                notes.load_text(f"Private local note · {run_tag}")
            await pilot.press("ctrl+s")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=20)
            await pilot.pause()

            refreshed = await _wait_for_task_id(backend, project.provider, tracked_task_id, run_tag)
            edited = refreshed if refreshed is not None else owned_tasks[0]
            if refreshed is None:
                _append_frame(
                    frame_specs,
                    "05-edit-saved",
                    "inline move attempted; task state still refreshing",
                    hold_scale=hold_scale,
                )
                _save_if_present(app, action_frames, frame_specs[-1].stem)
                actions.append(
                    {
                        "action": "tui-edit-move",
                        "task_id": tracked_task_id,
                        "provider_writes": 0,
                        "status": "pending-refresh",
                    }
                )
            elif move_requested and str(edited.column_id) != str(target_column):
                actions.append(
                    {
                        "action": "tui-edit-move",
                        "task_id": edited.task_id,
                        "provider_writes": 0,
                        "status": "saved-move-not-staged",
                    }
                )
                _append_frame(
                    frame_specs,
                    "05-edit-saved",
                    "inline save applied; move not staged by provider mapping",
                    hold_scale=hold_scale,
                )
                _save_if_present(app, action_frames, frame_specs[-1].stem)
            else:
                if move_requested:
                    actions.append({"action": "tui-edit-move", "task_id": edited.task_id, "provider_writes": 0})
                else:
                    actions.append(
                        {
                            "action": "tui-edit-move",
                            "task_id": edited.task_id,
                            "provider_writes": 0,
                            "status": "saved-no-move-change",
                        }
                    )
                _append_frame(
                    frame_specs,
                    "05-edit-saved",
                    "inline move saved in local draft",
                    hold_scale=hold_scale,
                )
                _save_frame(app, action_frames, frame_specs[-1].stem)
            view._select(str(edited.task_id))
            await pilot.pause()

            _append_frame(
                frame_specs,
                "00-filtered-split",
                "filtered split with local cards",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            app.set_board_layout(BoardLayout.ROWS)
            await pilot.pause()
            _append_frame(
                frame_specs,
                "01-filtered-rows",
                "filtered rows to review movement context",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            await pilot.press("v")
            await pilot.pause()
            _append_frame(
                frame_specs,
                "02-open-popup",
                "card popup open from list",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            await pilot.press("escape")
            await pilot.pause()
            _append_frame(
                frame_specs,
                "03-popup-closed",
                "card popup closed",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            await pilot.press("e")
            await pilot.pause()
            summary = _query_typed(app, "#work-item-edit-summary", Input)
            if summary is not None:
                summary.value = _enterprise_summary(project.provider, run_tag, "Sprint execution")
            description = _query_typed(app, "#work-item-edit-description", TextArea)
            if description and not description.disabled:
                current = _safe_text(description, "")
                description.load_text(current + f"\nAction timeline for {run_tag} ({project.provider}).")
            notes = _query_typed(app, "#work-item-edit-private-notes", TextArea)
            if notes:
                notes.load_text(f"Private note · enterprise workflow proof for {project.provider}.")
            status = _query_typed(app, "#work-item-edit-status", Select)
            if status is not None and not status.disabled:
                status.value = str(target_column)
            _append_frame(
                frame_specs,
                "04-edit-open",
                "inline editor open",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            await pilot.press("ctrl+s")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=20)
            await pilot.pause()
            _append_frame(
                frame_specs,
                "05-edit-saved",
                "inline move saved in local draft",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)
            actions.append({"action": "tui-edit-move", "task_id": edited.task_id, "provider_writes": 0})

            await pilot.press("escape")
            await pilot.pause()
            _append_frame(
                frame_specs,
                "06-edit-closed",
                "inline editor closed",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            view._select(str(edited.task_id))
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            _append_frame(
                frame_specs,
                "09b-card-cycle-open",
                "adjacent owned card opened for enterprise review",
                hold_scale=hold_scale,
            )
            _save_if_present(app, action_frames, frame_specs[-1].stem)
            summary = _query_typed(app, "#work-item-edit-summary", Input)
            if summary is not None:
                summary.value = _enterprise_summary(project.provider, run_tag, "Stakeholder Review")
            await pilot.press("ctrl+s")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=20)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            _append_frame(
                frame_specs,
                "09c-card-cycle-close",
                "adjacent card close and return",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            tabs = _query_typed(app, "#work-item-tabs", TabbedContent)
            if tabs is None:
                raise RuntimeError(f"{project.provider} split tabs unavailable")
            tabs.active = "work-item-comments-tab"
            await pilot.pause()
            _append_frame(
                frame_specs,
                "07-comments-tab",
                "comments tab selected",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            composer = _query_typed(view, "#work-item-comment-draft", TextArea)
            if composer is not None and not composer.disabled:
                composer.load_text(
                    f"[{run_tag}:{project.provider}] Sync notes and execution intent for enterprise workflow."
                )
                await pilot.click("#work-item-comment-add-local")
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=20)
                await pilot.pause()
                actions.append({"action": "comment-draft", "task_id": edited.task_id, "provider_writes": 0})
                _append_frame(
                    frame_specs,
                    "08-comment-draft",
                    "local comment draft added",
                    hold_scale=hold_scale,
                )
                _save_frame(app, action_frames, frame_specs[-1].stem)
            else:
                actions.append({"action": "comment-draft-deferred", "reason": "comments unsupported"})
                _append_frame(
                    frame_specs,
                    "08-comment-draft",
                    "comments unsupported on selection",
                    hold_scale=hold_scale,
                )
                _save_frame(app, action_frames, frame_specs[-1].stem)

            tabs.active = "work-item-info-tab"
            await pilot.pause()
            _append_frame(
                frame_specs,
                "09-tab-info",
                "returned to info tab",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            paths = _owned_paths(workspace, project, run_tag)
            if len(paths) < 2:
                raise RuntimeError(f"{project.provider} has insufficient owned cards for markdown timeline")
            second = next((path for path in paths if "card-02" in path.name), paths[1])
            raw_markdown_edit(second, run_tag=run_tag, provider=project.provider)
            backend.reload_local()
            await app.action_refresh_board()
            await pilot.pause()
            _append_frame(
                frame_specs,
                "10-raw-markdown-refresh",
                "raw markdown edited card visible after refresh",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)
            actions.append({"action": "raw-markdown-edit", "file": second.name, "provider_writes": 0})

            await pilot.press("f5")
            await _wait_for_screen(app, screen_type=SyncConfirmScreen, timeout=8.0)
            _append_frame(
                frame_specs,
                "11-sync-preview",
                "sync preview opened",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            if sync_mode == "pull":
                if _query_widget(app, "#sync-pull") is None:
                    await pilot.press("tab")
                if _query_widget(app, "#sync-pull") is not None:
                    await pilot.click("#sync-pull")
            elif sync_mode == "send":
                await pilot.press("y")
            else:
                cancel_button = _query_widget(app, "#sync-cancel")
                if cancel_button is not None:
                    with contextlib.suppress(Exception):
                        await pilot.click("#sync-cancel")
                if cancel_button is None or isinstance(app.screen, SyncConfirmScreen):
                    await pilot.press("escape")
            # Pull mode usually returns to board after processing sync.
            try:
                await _wait_for_screen(app, screen_type=WorkItemsView, timeout=45.0)
            except RuntimeError:
                actions.append({"action": "sync-close-timeout", "provider": project.provider, "provider_writes": 0})
                if not isinstance(app.screen, WorkItemsView):
                    await pilot.press("escape")
                    await pilot.pause()

            _append_frame(
                frame_specs,
                "12-sync-open",
                "sync preview action confirmed",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)

            if sync_mode == "pull":
                _append_frame(
                    frame_specs,
                    "12-sync-pull",
                    "sync pull-only completed",
                    hold_scale=hold_scale,
                )
            elif sync_mode == "send":
                _append_frame(
                    frame_specs,
                    "12-sync-pull",
                    "sync send executed",
                    hold_scale=hold_scale,
                )
            else:
                _append_frame(
                    frame_specs,
                    "12-sync-pull",
                    "sync path finished",
                    hold_scale=hold_scale,
                )
            _save_frame(app, action_frames, frame_specs[-1].stem)
            _append_frame(
                frame_specs,
                "13-sync-mode-action",
                "sync mode action acknowledged",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)
            if sync_mode == "cancel":
                _append_frame(
                    frame_specs,
                    "14-sync-complete",
                    "sync canceled by operator",
                    hold_scale=hold_scale,
                )
                _save_frame(app, action_frames, frame_specs[-1].stem)
            else:
                _append_frame(
                    frame_specs,
                    "14-sync-complete",
                    "sync completed and returned to board",
                    hold_scale=hold_scale,
                )
                _save_frame(app, action_frames, frame_specs[-1].stem)
            _append_frame(
                frame_specs,
                "15-final-board",
                "board focused after completing workflow and sync action",
                hold_scale=hold_scale,
            )
            _save_frame(app, action_frames, frame_specs[-1].stem)
    finally:
        with contextlib.suppress(Exception):
            provider.close()

    manifest = {
        "schema": 1,
        "provider": project.provider,
        "project_id": project.project_id,
        "frames": [frame.__dict__ for frame in frame_specs],
    }
    (target / "timeline.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"provider": project.provider, "project_id": project.project_id, "actions": actions, "frames": ACTION_STEPS}


def _provider_gif_path(gif_root: Path, provider: str) -> Path:
    return gif_root / f"live-real-9x1-{provider}.gif"


def _build_gif(provider_root: Path, gif_root: Path) -> Path:
    manifest_path = provider_root / "timeline.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Missing timeline manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def _raster_png(path: Path) -> Path | None:
        if path.exists():
            try:
                with Image.open(path) as image:
                    image.verify()
                return path
            except Exception:
                return None
        svg_alt = path.with_suffix(".svg")
        if svg_alt.exists():
            raw = svg_alt.read_text(encoding="utf-8", errors="ignore").lstrip()
            if raw.startswith("<svg"):
                # Keep this helper PNG-first to avoid SVG raster dependencies.
                return None
        return None

    def _rasterize_svg(path: Path) -> Path | None:
        if not path.exists():
            return None
        png_path = path.with_suffix(".png")
        png_path.unlink(missing_ok=True)
        try:
            import resvg_py
        except (ImportError, OSError):
            pass
        else:
            png_path.write_bytes(resvg_py.svg_to_bytes(svg_path=str(path)))
            return png_path
        try:
            import cairosvg
        except (ImportError, OSError):
            pass
        else:
            cairosvg.svg2png(url=str(path), write_to=str(png_path))
            return png_path
        import shutil
        import subprocess
        import tempfile

        discovered = shutil.which("chrome") or shutil.which("google-chrome")
        candidates = (
            Path(discovered) if discovered else None,
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        )
        renderer = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
        if renderer is None:
            return None
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            view_box = raw.split('viewBox="', 1)[1].split('"', 1)[0]
            _x, _y, width, height = (round(float(value)) for value in view_box.split())
        except Exception:
            return None
        with tempfile.TemporaryDirectory(prefix="pykantui-live-actions-shot-") as profile:
            arguments = (
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                f"--user-data-dir={profile}",
                "--force-device-scale-factor=1",
                f"--window-size={width},{height}",
                f"--screenshot={png_path.resolve()}",
                path.resolve().as_uri(),
            )
            escaped = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
            command = (
                f"$p=Start-Process -FilePath '{renderer}' -ArgumentList @({escaped}) "
                "-Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
            if result.returncode != 0 or not png_path.is_file():
                return None
        return png_path

    def _raster_png_or_svg(path: Path) -> Path | None:
        png_path = _raster_png(path)
        if png_path is not None and png_path.exists():
            return png_path
        if path.with_suffix(".svg").exists():
            return _rasterize_svg(path.with_suffix(".svg"))
        return None

    frames = [frame["stem"] for frame in manifest["frames"]]
    frame_durations = {
        item["stem"]: item.get("hold_seconds", HOLD_SECONDS[item["stem"]])
        for item in manifest["frames"]
    }
    images: list[Image.Image] = []
    durations: list[int] = []
    for frame in frames:
        image_path = provider_root / "actions" / f"{frame}.png"
        png_path = _raster_png_or_svg(image_path)
        if png_path is None or not png_path.exists():
            continue
        images.append(Image.open(png_path).convert("RGB"))
        durations.append(int(round(frame_durations[frame] * 1000)))

    if not images:
        raise RuntimeError(f"No frames found for GIF for {provider_root.name}")
    gif_root.mkdir(parents=True, exist_ok=True)
    out = _provider_gif_path(gif_root, provider_root.name)
    first, *rest = images
    first.save(
        out,
        save_all=True,
        append_images=rest,
        duration=durations,
        loop=0,
    )
    for image in images:
        image.close()
    return out


def build_gifs(run_root: Path, run_tag: str, gif_root: Path, providers: tuple[str, ...]) -> list[Path]:
    outputs = []
    for provider in providers:
        provider_root = run_root / run_tag / "live-local" / provider
        if not provider_root.exists():
            continue
        with contextlib.suppress(RuntimeError):
            outputs.append(_build_gif(provider_root, gif_root))
    return outputs


async def run_all(
    workspace_root: Path,
    artifact_root: Path,
    *,
    run_tag: str,
    providers: tuple[str, ...] = PROVIDERS,
    sync_mode: str = "cancel",
    hold_scale: float = 1.0,
) -> Path:
    results = []
    for provider in providers:
        try:
            results.append(
                await exercise(
                    workspace_root / provider,
                    artifact_root,
                    run_tag=run_tag,
                    sync_mode=sync_mode,
                    hold_scale=hold_scale,
                )
            )
        except Exception as exc:
            error_path = (
                artifact_root
                / run_tag
                / "live-local"
                / provider
                / "exercise-error.txt"
            )
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            results.append({"provider": provider, "error": str(exc)})
    suffix = "all" if providers == PROVIDERS else "-".join(providers)
    target = artifact_root / run_tag / "live-local" / f"local-actions-{suffix}.json"
    target.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--provider", action="append", choices=PROVIDERS)
    parser.add_argument(
        "--sync-mode",
        choices=("cancel", "pull", "send"),
        default="cancel",
        help="sync button to exercise after sync preview",
    )
    parser.add_argument(
        "--make-gifs",
        action="store_true",
        help="build one GIF per provider from captured action frames",
    )
    parser.add_argument(
        "--gif-dir",
        type=Path,
        default=Path("assets"),
        help="directory for generated GIF output",
    )
    parser.add_argument(
        "--hold-scale",
        type=float,
        default=1.0,
        help="scale factor for per-frame hold duration",
    )
    args = parser.parse_args()
    selected = tuple(args.provider) if args.provider else PROVIDERS
    result = asyncio.run(
        run_all(
            args.workspace_root.resolve(),
            args.artifacts.resolve(),
            run_tag=args.run_tag,
            providers=selected,
            sync_mode=args.sync_mode,
            hold_scale=args.hold_scale,
        )
    )

    if args.make_gifs:
        # Keep this helper intentionally local to action collection.
        if args.sync_mode == "send":
            print("warning: --sync-mode send may trigger remote writes")
        artifacts = args.artifacts.resolve()
        gifs = build_gifs(artifacts, args.run_tag, args.gif_dir.resolve(), selected)
        print("gif outputs:")
        for path in gifs:
            print(f"  {path}")

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


