"""Capture genuine Textual Split layouts for provider field-density tiers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, TypeVar

os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from textual.containers import VerticalScroll
from textual.widgets import Button

from pykantui.core.work_items import WorkItemColumn
from pykantui.models import BoardLayout, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.registry import get
from pykantui.tracker.spec import ProviderSpec
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView

try:
    from tools.split_sidebar_screenshots import _rasterise
except ModuleNotFoundError:  # Direct ``python tools/...`` execution.
    from split_sidebar_screenshots import _rasterise  # type: ignore[no-redef]

SIZE = (150, 40)
BackendT = TypeVar("BackendT", bound=JsonBackend)


class OfflineProviderBackend(JsonBackend):
    """Local data paired with a real provider's static field contract."""

    supports_issue_fields = True
    supports_reorder = False
    supports_sync = True

    def __init__(self, provider_name: str) -> None:
        self.provider_spec: ProviderSpec = get(provider_name).spec
        super().__init__()

    def display_kind(self) -> str:
        return self.provider_spec.label

    def available_task_fields(self) -> frozenset[WorkItemColumn]:
        return self.provider_spec.available_table_fields({})

    def editable_task_fields(self) -> frozenset[str]:
        return frozenset(self.provider_spec.editable_card_fields({}))

    def supports_private_notes(self) -> bool:
        return True


def _populate(backend: BackendT) -> BackendT:
    """Add the same representative work item to every offline comparison."""
    backend.create_task(
        Task(
            task_id=1,
            title="Provider-aware Split sidebar",
            column_id=2,
            description="The initial width follows the provider's declared field density.",
            metadata={
                "key": "DEMO-1",
                "issue_type": "Story",
                "assignee": "Alex Morgan",
                "reporter": "Platform team",
                "priority": "High",
                "labels": ["tui", "layout"],
                "components": ["Desktop"],
                "private_notes": "The user-adjusted ratio remains stable.",
                "sync_status": "synced",
            },
        )
    )
    return backend


def _backend(provider_name: str) -> OfflineProviderBackend:
    return _populate(OfflineProviderBackend(provider_name))


def _local_backend() -> JsonBackend:
    return _populate(JsonBackend())


def _metrics(app: KanbanApp, provider: str, state: str) -> dict[str, Any]:
    view = app.query_one(WorkItemsView)
    left = app.query_one("#work-items-list-pane")
    divider = app.query_one("#work-item-resizer")
    right = app.query_one("#work-item-detail-pane")
    scroll = app.query_one("#work-item-edit-scroll", VerticalScroll)
    save = app.query_one("#work-item-edit-save", Button)
    cancel = app.query_one("#work-item-edit-cancel", Button)
    available = app.backend.available_task_fields()
    editable = app.backend.editable_task_fields()
    if divider.region.width != 1:
        raise RuntimeError(f"divider is {divider.region.width} cells, expected 1")
    if scroll.max_scroll_x != 0:
        raise RuntimeError(f"sidebar has horizontal overflow: {scroll.max_scroll_x}")
    if save.region.right > right.content_region.right or cancel.region.right > right.content_region.right:
        raise RuntimeError("sidebar action buttons are clipped")
    return {
        "provider": provider,
        "state": state,
        "terminal_cells": list(SIZE),
        "visible_field_count": len(available),
        "editable_field_count": len(editable),
        "default_list_percent": view.default_list_percent,
        "requested_list_percent": view.list_percent,
        "rows_pane_cells": [left.region.width, left.region.height],
        "divider_cells": [divider.region.width, divider.region.height],
        "detail_pane_cells": [right.region.width, right.region.height],
        "detail_scroll_viewport_cells": [scroll.content_region.width, scroll.content_region.height],
        "detail_scroll_virtual_cells": [scroll.virtual_size.width, scroll.virtual_size.height],
        "vertical_scrollbar_cells": scroll.scrollbar_size_vertical,
        "horizontal_scroll_max": scroll.max_scroll_x,
        "save_region": [save.region.x, save.region.y, save.region.width, save.region.height],
        "cancel_region": [cancel.region.x, cancel.region.y, cancel.region.width, cancel.region.height],
        "inline_editor": view.editing,
    }


def _save(app: KanbanApp, output: Path, name: str) -> Path:
    svg = output / f"{name}.svg"
    app.save_screenshot(str(svg.resolve()))
    return svg


def _png(svg: Path) -> Path:
    """Rasterize the compositor SVG exactly, without recreating the UI."""
    png = _rasterise(svg)
    if png is not None:
        return png
    png = svg.with_suffix(".png")
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    view_box = svg.read_text(encoding="utf-8").split('viewBox="', 1)[1].split('"', 1)[0]
    _x, _y, width, height = (round(float(value)) for value in view_box.split())
    result: subprocess.CompletedProcess[bytes] | None = None
    for _attempt in range(3):
        time.sleep(1)
        with tempfile.TemporaryDirectory(prefix="pykantui-density-shot-") as profile:
            arguments = (
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                f"--user-data-dir={profile}",
                "--force-device-scale-factor=1",
                f"--window-size={width},{height}",
                f"--screenshot={png.resolve()}",
                svg.resolve().as_uri(),
            )
            escaped = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
            command = (
                f"$p=Start-Process -FilePath '{chrome}' -ArgumentList @({escaped}) "
                "-Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
        if not result.returncode and png.is_file():
            break
    if result is None or result.returncode or not png.is_file():
        detail = (
            ""
            if result is None
            else " | ".join(
                (
                    f"exit={result.returncode}",
                    result.stdout.decode(errors="replace").strip(),
                    result.stderr.decode(errors="replace").strip(),
                )
            )
        )
        raise RuntimeError(f"no local SVG renderer could rasterize {svg}: {detail}")
    return png


async def _capture_provider(
    output: Path,
    provider_name: str,
    *,
    include_adjustment: bool = False,
) -> tuple[list[Path], dict[str, dict[str, Any]]]:
    app = KanbanApp(_backend(provider_name), confirm_moves=False)
    paths: list[Path] = []
    measurements: dict[str, dict[str, Any]] = {}
    async with app.run_test(size=SIZE) as pilot:
        app.theme = "cyberpunk"
        await pilot.pause()
        app.set_board_layout(BoardLayout.SPLIT)
        await pilot.pause()
        view = app.query_one(WorkItemsView)
        await view.start_inline_edit()
        await pilot.pause()
        view.action_focus_tab("details")
        await pilot.pause()

        name = f"split-density-{provider_name}-default"
        paths.append(_save(app, output, name))
        measurements[name] = _metrics(app, provider_name, "provider default")

        if include_adjustment:
            view.action_grow_list()
            view.action_grow_list()
            await pilot.pause()
            adjusted_name = f"split-density-{provider_name}-adjusted"
            paths.append(_save(app, output, adjusted_name))
            measurements[adjusted_name] = _metrics(app, provider_name, "user adjusted +10")

            view.action_reset_split()
            await pilot.pause()
            reset_name = f"split-density-{provider_name}-reset"
            paths.append(_save(app, output, reset_name))
            measurements[reset_name] = _metrics(app, provider_name, "reset to provider default")

    return paths, measurements


async def _capture_local(output: Path) -> tuple[list[Path], dict[str, dict[str, Any]]]:
    app = KanbanApp(_local_backend(), confirm_moves=False)
    async with app.run_test(size=SIZE) as pilot:
        app.theme = "cyberpunk"
        await pilot.pause()
        app.set_board_layout(BoardLayout.SPLIT)
        await pilot.pause()
        view = app.query_one(WorkItemsView)
        await view.start_inline_edit()
        await pilot.pause()
        view.action_focus_tab("details")
        await pilot.pause()

        name = "split-density-json-default"
        svg = _save(app, output, name)
        return [svg], {name: _metrics(app, "json", "local default")}


async def render(output: Path) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    local_paths, local_metrics = await _capture_local(output)
    compact_paths, compact_metrics = await _capture_provider(output, "asana")
    dense_paths, dense_metrics = await _capture_provider(output, "jira", include_adjustment=True)
    metrics_path = output / "metrics.json"
    metrics_path.write_text(
        json.dumps(local_metrics | compact_metrics | dense_metrics, indent=2),
        encoding="utf-8",
    )
    paths = local_paths + compact_paths + dense_paths
    return tuple([*paths, metrics_path])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, default=Path("artifacts/provider-density-split"))
    parser.add_argument(
        "--rasterize-existing",
        action="store_true",
        help="Rasterize existing compositor SVGs in a fresh process.",
    )
    arguments = parser.parse_args()
    if arguments.rasterize_existing:
        paths = tuple(_png(svg) for svg in sorted(arguments.into.glob("split-density-*.svg")))
    else:
        paths = asyncio.run(render(arguments.into))
    for path in paths:
        print(path.resolve())


if __name__ == "__main__":
    main()
