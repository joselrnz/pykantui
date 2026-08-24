"""Build deterministic, network-free evidence bundles for provider workflows.

The default command captures every provider with Textual's real compositor.
``--manifest-only`` is a fast planning mode for CI and for preparing a later
run-tagged workspace.  No credential loader or provider HTTP client is used.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pykantui.tracker.registry import specs

EVIDENCE_PHASES = (
    "before",
    "create-local",
    "markdown-edit",
    "tui-edit",
    "move",
    "comment-draft",
    "sync-result",
    "api-validated",
    "conflict",
)

_BASE_CASES: tuple[tuple[str, str], ...] = (
    ("cards-20-plus", "27 cards preserve stable provider identity"),
    ("comments-20-plus", "23 comments render, scroll, and round-trip"),
    ("unicode", "CJK, RTL, accents, combining marks, and emoji survive"),
    ("markdown-marker", "owned markers inside provider text cannot inject regions"),
    ("long-text", "long title, description, notes, and comment remain reachable"),
    ("blank-optional", "blank optional values remain absent rather than invented"),
    ("duplicate-title", "duplicate titles retain distinct opaque ids"),
    ("null-people", "missing assignee and reporter remain readable"),
    ("status", "unknown and known provider statuses remain distinguishable"),
    ("due-boundary", "overdue, today, future, and missing due dates render"),
    ("labels", "empty, repeated, Unicode, and punctuation labels round-trip"),
    ("comment-draft", "local comment draft never writes before confirmation"),
    ("conflict", "same-field local and provider edits require a decision"),
    ("ambiguous", "unknown write outcome is held and never auto-replayed"),
)

_PROVIDER_TYPES: dict[str, tuple[str, ...]] = {
    "asana": ("Task", "Milestone"),
    "clickup": ("Task", "Subtask"),
    "forgejo": ("Issue",),
    "github": ("Issue", "Pull request"),
    "jira": ("Task", "Bug", "Story", "Epic", "Subtask"),
    "linear": ("Issue", "Sub-issue"),
    "monday": ("Item", "Subitem"),
    "plane": ("Issue", "Sub-issue"),
    "shortcut": ("Story", "Bug", "Chore"),
    "trello": ("Card", "Checklist item"),
}

_SAFE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[/\\]")
_SECRET_KEYS = ("token", "secret", "password", "authorization", "cookie", "api_key", "apikey")

_ENTERPRISE_PROJECTS = {
    "asana": ("OPS", "Portfolio Operations"),
    "clickup": ("CLP", "Client Platform"),
    "forgejo": ("FOR", "Forge Platform"),
    "github": ("API", "API Platform"),
    "jira": ("PAY", "Payment Modernization"),
    "linear": ("INF", "Product Infrastructure"),
    "monday": ("LCH", "Launch Operations"),
    "plane": ("COR", "Core Services"),
    "shortcut": ("MOB", "Mobile Engineering"),
    "trello": ("SEC", "Security Program"),
}
_ENTERPRISE_CARD_TITLES = (
    "Finalize SSO rollout",
    "Rotate production signing keys",
    "Publish incident response runbook",
    "Migrate audit log pipeline",
    "Add regional failover checks",
    "Resolve billing reconciliation gaps",
    "Harden webhook signature validation",
    "Automate database recovery drill",
    "Reduce checkout latency",
    "Update data retention controls",
    "Add accessibility keyboard audit",
    "Reconcile usage metering",
    "Expand contract test coverage",
    "Document disaster recovery ownership",
    "Patch dependency vulnerability",
    "Add customer export throttling",
    "Validate backup restoration",
    "Improve queue saturation alerts",
    "Add deployment approval policy",
    "Complete privacy impact review",
    "Migrate object storage lifecycle",
    "Fix duplicate notification events",
    "Add tenant isolation metrics",
    "Review administrator permissions",
    "Automate release rollback checks",
    "Update API pagination contract",
    "Prepare quarterly reliability report",
)


@dataclass(frozen=True, slots=True)
class EnterpriseFixture:
    """Synthetic, provider-specific data used only for public visual assets."""

    project_key: str
    project_name: str
    card_titles: tuple[str, ...]


def build_enterprise_fixture(provider_name: str) -> EnterpriseFixture:
    """Return a deterministic public fixture with no account-derived identity."""

    key, name = _ENTERPRISE_PROJECTS[provider_name]
    return EnterpriseFixture(project_key=key, project_name=name, card_titles=_ENTERPRISE_CARD_TITLES)


def build_edge_cases() -> list[dict[str, object]]:
    """Return the deterministic cross-provider evidence catalogue."""
    cases: list[dict[str, object]] = []
    for provider_spec in specs():
        provider = provider_spec.name
        ordinal = 0

        def append(
            category: str,
            title: str,
            *,
            value: str = "",
            bound_provider: str = provider,
            bound_label: str = provider_spec.label,
        ) -> None:
            nonlocal ordinal
            ordinal += 1
            cases.append(
                {
                    "case_id": f"{bound_provider}-{ordinal:03d}-{category}",
                    "provider": bound_provider,
                    "provider_label": bound_label,
                    "category": category,
                    "title": title,
                    "value": value,
                    "network": "forbidden",
                }
            )

        for category, title in _BASE_CASES:
            append(category, title)

        fields = sorted(
            {
                *(str(item.value) for item in provider_spec.available_table_fields({})),
                *provider_spec.capabilities.writable_fields,
                *provider_spec.creatable_card_fields({}),
            }
        )
        for field in fields or ["title"]:
            append("provider-field", f"{provider_spec.label} field {field!r} round-trips", value=field)
        for issue_type in _PROVIDER_TYPES[provider]:
            append(
                "provider-type",
                f"{provider_spec.label} type {issue_type!r} remains provider-owned",
                value=issue_type,
            )
    return cases


def build_action_manifest(run_tag: str) -> dict[str, Any]:
    """Plan the required screenshots without exposing a local absolute path."""
    if not _SAFE_TAG.fullmatch(run_tag):
        raise ValueError("run tag must contain only letters, numbers, dot, underscore, or dash")
    actions: list[dict[str, Any]] = []
    for provider_spec in specs():
        for number, phase in enumerate(EVIDENCE_PHASES, start=1):
            actions.append(
                {
                    "action_id": f"{provider_spec.name}-{number:02d}-{phase}",
                    "provider": provider_spec.name,
                    "provider_label": provider_spec.label,
                    "phase": phase,
                    "workspace": f"workspaces/{provider_spec.name}",
                    "screenshots": {
                        "svg": f"{run_tag}/{provider_spec.name}/{number:02d}-{phase}.svg",
                        "png": f"{run_tag}/{provider_spec.name}/{number:02d}-{phase}.png",
                    },
                    "status": "planned",
                    "network": "forbidden",
                    "evidence_kind": "offline-simulated",
                    "artifacts": None,
                    "geometry": None,
                }
            )
    return {
        "schema": 1,
        "run_tag": run_tag,
        "mode": "network-free",
        "evidence_kind": "offline-simulated",
        "validation_scope": "in-memory normalized provider source of truth",
        "live_api_receipts": [],
        "providers": [item.name for item in specs()],
        "required_phases": list(EVIDENCE_PHASES),
        "edge_cases": build_edge_cases(),
        "counts": {
            "providers": len(specs()),
            "phases": len(EVIDENCE_PHASES),
            "actions": len(actions),
            "edge_cases": len(build_edge_cases()),
            "artifacts": 0,
        },
        "actions": actions,
    }


def record_artifact(root: Path, artifact: Path) -> dict[str, object]:
    """Describe one artifact by a portable relative path and content hash."""
    resolved_root = root.resolve()
    resolved = artifact.resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("artifact must stay inside the evidence root") from error
    content = resolved.read_bytes()
    if not content:
        raise ValueError("artifact is empty")
    return {
        "path": relative.as_posix(),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def validate_svg(path: Path) -> dict[str, object]:
    """Reject empty compositor output and report its exact SVG geometry."""
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as error:
        raise ValueError(f"invalid SVG: {path.name}") from error
    view_box = [_svg_number(value) for value in root.attrib.get("viewBox", "").split()]
    if view_box and (len(view_box) != 4 or view_box[2] <= 0 or view_box[3] <= 0):
        raise ValueError("SVG viewBox must have positive geometry")
    width = _svg_number(root.attrib.get("width", "0"))
    height = _svg_number(root.attrib.get("height", "0"))
    if width <= 0 and len(view_box) == 4:
        width = view_box[2]
    if height <= 0 and len(view_box) == 4:
        height = view_box[3]
    if width <= 0 or height <= 0:
        raise ValueError("SVG width and height must be positive")
    visible = sum(
        1
        for node in root.iter()
        if node is not root and _local_name(node.tag) not in {"defs", "style", "metadata", "title"}
    )
    if visible <= 0:
        raise ValueError("SVG contains no visible nodes")
    return {
        "pixels": [width, height],
        "view_box": view_box,
        "visible_nodes": visible,
    }


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """Fail closed on omissions, absolute paths, credentials, or weak hashes."""
    actions = manifest.get("actions")
    if not isinstance(actions, list):
        raise ValueError("manifest actions must be a list")
    expected = {(spec.name, phase) for spec in specs() for phase in EVIDENCE_PHASES}
    actual = {
        (str(action.get("provider", "")), str(action.get("phase", "")))
        for action in actions
        if isinstance(action, Mapping)
    }
    if actual != expected:
        raise ValueError("manifest does not contain every provider evidence phase")

    for key, value in _walk(manifest):
        lowered = key.lower()
        if any(secret in lowered for secret in _SECRET_KEYS):
            raise ValueError(f"secret-shaped manifest key is forbidden: {key}")
        if (
            isinstance(value, str)
            and key in {"workspace", "screenshot", "screenshots", "path", "svg", "png"}
            and (value.startswith(("/", "\\")) or _DRIVE_PATH.match(value))
        ):
            raise ValueError(f"absolute workspace or artifact path is forbidden: {value}")
        if isinstance(value, Mapping) and {"path", "bytes", "sha256"}.intersection(value):
            digest = str(value.get("sha256", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("artifact sha256 must be a full lowercase digest")


def write_manifest(root: Path, manifest: dict[str, Any]) -> Path:
    """Validate then atomically publish a run manifest."""
    from pykantui.config.paths import write_text_atomic

    validate_manifest(manifest)
    target = root / str(manifest["run_tag"]) / "manifest.json"
    write_text_atomic(target, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return target


def write_index(root: Path, manifest: Mapping[str, Any]) -> Path:
    """Write a portable PNG gallery next to one run manifest."""
    from pykantui.config.paths import write_text_atomic

    run_tag = str(manifest["run_tag"])
    lines = [
        f"# Provider evidence · {run_tag}",
        "",
        "> Offline simulated evidence. API validation here compares the local workspace "
        "with an in-memory normalized provider source of truth; it is not a live API receipt.",
        "",
        "| Provider | Phase | Screenshot | SHA-256 |",
        "|---|---|---|---|",
    ]
    for action in manifest.get("actions", []):
        if not isinstance(action, Mapping) or action.get("status") != "captured":
            continue
        artifacts = action.get("artifacts")
        if not isinstance(artifacts, Mapping) or not isinstance(artifacts.get("png"), Mapping):
            continue
        png = artifacts["png"]
        path = Path(str(png["path"]))
        try:
            relative = path.relative_to(run_tag).as_posix()
        except ValueError as error:
            raise ValueError("gallery artifact must be inside its run tag") from error
        label = f"{action['provider_label']} {action['phase']}"
        lines.append(
            f"| {action['provider_label']} | {action['phase']} | "
            f"![{label}]({relative}) | `{str(png['sha256'])[:16]}…` |"
        )
    target = root / run_tag / "index.md"
    write_text_atomic(target, "\n".join(lines) + "\n")
    return target


def validate_png(path: Path) -> dict[str, object]:
    """Verify raster geometry and reject a blank, single-color screenshot."""
    from PIL import Image

    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            colors = image.convert("RGB").getcolors(maxcolors=1_000_000)
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid PNG: {path.name}") from error
    if width <= 0 or height <= 0:
        raise ValueError("PNG width and height must be positive")
    color_count = 1_000_001 if colors is None else len(colors)
    if color_count < 2:
        raise ValueError("PNG contains no visible color variation")
    return {"pixels": [width, height], "colors": color_count}


def rasterise_svg(svg_path: Path) -> Path:
    """Rasterize one genuine compositor SVG with a local headless browser."""
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    renderer = next((candidate for candidate in candidates if candidate.is_file()), None)
    if renderer is None:
        raise RuntimeError("Chrome or Edge is required to rasterize evidence SVGs")
    geometry = validate_svg(svg_path)
    view_box = cast(list[float], geometry["view_box"])
    pixels = cast(list[float], geometry["pixels"])
    width = round(float(view_box[2] if len(view_box) == 4 else pixels[0]))
    height = round(float(view_box[3] if len(view_box) == 4 else pixels[1]))
    png_path = svg_path.with_suffix(".png")
    png_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="pykantui-provider-evidence-") as profile:
        arguments = (
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            f"--user-data-dir={profile}",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            f"--screenshot={png_path.resolve()}",
            svg_path.resolve().as_uri(),
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
        if not result.returncode:
            for _ in range(100):
                if png_path.is_file():
                    break
                time.sleep(0.05)
    if result.returncode or not png_path.is_file():
        raise RuntimeError(f"could not rasterize {svg_path.name}")
    validate_png(png_path)
    return png_path


def _walk(value: object, key: str = "") -> list[tuple[str, object]]:
    found = [(key, value)]
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            found.extend(_walk(child, str(child_key)))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk(child, key))
    return found


def _svg_number(value: str) -> float:
    match = re.match(r"^[ ]*([0-9]+(?:\.[0-9]+)?)", value)
    return float(match.group(1)) if match else 0.0


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


async def capture_evidence(
    root: Path,
    run_tag: str,
    *,
    provider_names: set[str] | None = None,
) -> Path:
    """Capture every planned phase with real Textual compositor output."""
    manifest = build_action_manifest(run_tag)
    selected = provider_names or {spec.name for spec in specs()}
    unknown = selected - {spec.name for spec in specs()}
    if unknown:
        raise ValueError(f"unknown evidence providers: {', '.join(sorted(unknown))}")
    for provider in sorted(selected):
        provider_actions = [action for action in manifest["actions"] if action["provider"] == provider]
        await _capture_provider_journey(root, run_tag, provider, provider_actions)
        for action in provider_actions:
            svg_path = root / str(action["screenshots"]["svg"])
            png_path = rasterise_svg(svg_path)
            action["artifacts"] = {
                "svg": record_artifact(root, svg_path),
                "png": record_artifact(root, png_path),
            }
            action["geometry"] = {
                "svg": validate_svg(svg_path),
                "png": validate_png(png_path),
            }
            action["status"] = "captured"
    manifest["counts"]["artifacts"] = 2 * sum(action["status"] == "captured" for action in manifest["actions"])
    manifest_path = write_manifest(root, manifest)
    write_index(root, manifest)
    return manifest_path


async def _capture_provider_journey(
    root: Path,
    run_tag: str,
    provider_name: str,
    actions: list[dict[str, Any]],
) -> None:
    """Execute one genuine local-first journey against an in-memory source of truth."""
    from datetime import UTC, datetime

    from tests.integration.sync.test_push import COLUMNS, TODO, RecordingProvider, issue
    from textual.widgets import Input

    from pykantui.commands.new import write_draft
    from pykantui.models import BoardLayout
    from pykantui.pages.detail import TaskDetailScreen
    from pykantui.pages.sync import SyncConfirmScreen
    from pykantui.sync.provider import ProviderBackend
    from pykantui.tracker.models import CommentDraft, IssueDraft, RemoteComment, RemoteIssue, RemoteProject
    from pykantui.tracker.registry import get
    from pykantui.tui.app import KanbanApp
    from pykantui.tui.widgets.card import TaskCard
    from pykantui.tui.widgets.comments import CommentsPane
    from pykantui.tui.widgets.work_items import WorkItemsView
    from pykantui.workspace.project import Project
    from pykantui.workspace.sync import sync

    class EvidenceProvider(RecordingProvider):
        def __init__(self, issues: list[RemoteIssue]) -> None:
            super().__init__(issues)
            self.remote_comments: dict[str, list[RemoteComment]] = {}

        def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
            del project_id
            made = issue(
                f"{provider_name[:3].upper()}-NEW-{len(self._issues) + 1}",
                TODO,
                title=draft.title,
                body=draft.body,
            )
            self._issues.append(made)
            return made

        def iter_comments(self, project_id: str, issue: RemoteIssue):  # type: ignore[no-untyped-def]
            del project_id
            yield from self.remote_comments.get(issue.issue_id, ())

        def create_comment(
            self,
            project_id: str,
            issue: RemoteIssue,
            draft: CommentDraft,
        ) -> RemoteComment:
            del project_id
            made = RemoteComment(
                comment_id=f"remote-{len(self.remote_comments.get(issue.issue_id, ())) + 1}",
                issue_id=issue.issue_id,
                body=draft.body,
                author="Evidence API",
                created_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
            )
            self.remote_comments.setdefault(issue.issue_id, []).append(made)
            return made

    spec = get(provider_name).spec
    fixture = build_enterprise_fixture(provider_name)
    project = RemoteProject(
        project_id=f"{provider_name}-public-demo",
        key=fixture.project_key,
        name=fixture.project_name,
    )
    prefix = provider_name[:3].upper()
    provider = EvidenceProvider(
        [
            issue(
                f"{prefix}-{number:02d}",
                COLUMNS[(number - 1) % len(COLUMNS)],
                title=fixture.card_titles[number - 1],
                body=f"Program work item {number:02d} · Markdown, Unicode 測試, and audit history preserved.",
                issue_type=_PROVIDER_TYPES[provider_name][0],
                labels=("evidence", f"case-{number:02d}"),
            )
            for number in range(1, 28)
        ]
    )
    provider.spec = spec  # type: ignore[misc]
    workspace = root / run_tag / "workspaces" / provider_name
    workspace.mkdir(parents=True, exist_ok=True)
    sync(workspace, provider, project, push_edits=False, commit=False)
    backend = ProviderBackend(workspace, provider, project)
    by_phase = {str(action["phase"]): action for action in actions}

    def save(app: KanbanApp, phase: str) -> None:
        action = by_phase[phase]
        artifact = root / str(action["screenshots"]["svg"])
        artifact.parent.mkdir(parents=True, exist_ok=True)
        app.title = f"{spec.label} · {fixture.project_name}"
        app.save_screenshot(str(artifact.resolve()))

    app = KanbanApp(backend, confirm_moves=False)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        save(app, "before")

        record = Project(
            provider=provider_name,
            project_id=project.project_id,
            key=project.key,
            name=project.name,
        )
        write_draft(
            workspace,
            record,
            TODO,
            IssueDraft(
                title="Document disaster recovery ownership",
                body="Created locally in Markdown and awaiting an approved sync.",
            ),
        )
        backend.reload_local()
        await app.action_refresh_board()
        await pilot.pause()
        save(app, "create-local")

        card_path = next(workspace.rglob(f"{prefix}-01.md"))
        original = card_path.read_text(encoding="utf-8")
        card_path.write_text(
            original.replace(
                f"title: {fixture.card_titles[0]}",
                "title: Publish incident response runbook",
            ),
            encoding="utf-8",
        )
        backend.reload_local()
        await app.action_refresh_board()
        await pilot.pause()
        save(app, "markdown-edit")

        first_card = next(card for card in app.query(TaskCard) if card.task_.metadata.get("key") == f"{prefix}-01")
        first_card.focus()
        await pilot.press("e")
        await pilot.pause()
        if not isinstance(app.screen, TaskDetailScreen):
            raise RuntimeError(f"{spec.label} TUI editor did not open")
        summary = app.screen.query_one("#detail-summary", Input)
        summary.value = "Finalize incident response runbook"
        save(app, "tui-edit")
        await pilot.press("ctrl+s")
        await pilot.pause()

        task = next(item for item in backend.get_tasks() if item.metadata.get("key") == f"{prefix}-01")
        if provider.spec.capabilities.move_issues:
            moved = backend.move_task(task, 2)
            if not moved.ok:
                raise RuntimeError(moved.message)
        await app.action_refresh_board()
        await pilot.pause()
        save(app, "move")

        task = next(item for item in backend.get_tasks() if item.metadata.get("key") == f"{prefix}-01")
        drafted = backend.save_comment_draft(task, "Security review complete; release approval pending.")
        if not drafted.ok:
            raise RuntimeError(drafted.message)
        await app.action_refresh_board()
        app.set_board_layout(BoardLayout.SPLIT)
        await pilot.pause()
        view = app.query_one(WorkItemsView)
        comment_task = next(item for item in backend.get_tasks() if item.metadata.get("key") == f"{prefix}-01")
        view._select(str(comment_task.task_id))  # noqa: SLF001 - evidence selects exact identity
        view.action_focus_tab("comments")
        pane = view.query_one("#work-item-comments-pane", CommentsPane)
        if pane._worker is not None:  # noqa: SLF001 - deterministic evidence owns this load
            pane._worker.cancel()  # noqa: SLF001
        pane._generation += 1  # noqa: SLF001
        await pane._load(comment_task, pane._generation)  # noqa: SLF001
        await pilot.pause()
        if not view.query(".pending-comment"):
            raise RuntimeError(f"{spec.label} local comment draft was not visible")
        save(app, "comment-draft")

        await asyncio.to_thread(backend.sync_now, confirm=lambda _plan: True, commit=False)
        app.set_board_layout(BoardLayout.KANBAN)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        save(app, "sync-result")

        local = next(item for item in backend.get_tasks() if item.metadata.get("key") == f"{prefix}-01")
        remote = next(item for item in provider._issues if item.key == f"{prefix}-01")
        if local.description != remote.body or not provider.remote_comments.get(remote.issue_id):
            raise RuntimeError(f"{spec.label} in-memory API source of truth did not match local sync")
        save(app, "api-validated")

        card_path = next(workspace.rglob(f"{prefix}-01.md"))
        text = card_path.read_text(encoding="utf-8")
        card_path.write_text(
            text.replace(
                "title: Finalize incident response runbook",
                "title: Resolve regional rollout conflict",
            ),
            encoding="utf-8",
        )
        provider._issues = [
            item.model_copy(update={"title": "Provider changed regional rollout scope"})
            if item.key == f"{prefix}-01"
            else item
            for item in provider._issues
        ]
        await pilot.press("f5")
        for _ in range(40):
            await pilot.pause()
            if isinstance(app.screen, SyncConfirmScreen) and app.screen.query("#sync-dialog"):
                break
        else:
            raise RuntimeError(f"{spec.label} conflict preview did not finish composing")
        await pilot.pause()
        save(app, "conflict")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, default=Path("artifacts/provider-evidence"))
    parser.add_argument("--run-tag", required=True)
    parser.add_argument(
        "--providers",
        help="comma-separated provider names (default: all shipped providers)",
    )
    parser.add_argument("--manifest-only", action="store_true")
    arguments = parser.parse_args()
    selected = (
        {name.strip().lower() for name in arguments.providers.split(",") if name.strip()}
        if arguments.providers
        else None
    )
    if arguments.manifest_only:
        manifest = build_action_manifest(arguments.run_tag)
        path = write_manifest(arguments.into, manifest)
        write_index(arguments.into, manifest)
    else:
        path = asyncio.run(capture_evidence(arguments.into, arguments.run_tag, provider_names=selected))
    print(path.resolve())


if __name__ == "__main__":
    main()
