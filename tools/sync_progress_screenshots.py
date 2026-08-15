"""Capture compositor evidence for the provider-neutral Sync progress dialog.

The harness mounts the production ``SyncProgressScreen`` inside the production
``KanbanApp``.  Its only fixture is the deterministic in-memory JSON backend;
no provider credentials, network calls, or substitute widgets are involved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Label, LoadingIndicator

from pykantui.pages.sync import SyncProgressScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.workspace.models import SyncReport
from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate

NORMAL_SIZE = (100, 30)
NARROW_SIZE = (80, 24)
TINY_SIZE = (40, 12)


@dataclass(frozen=True, slots=True)
class Scenario:
    """One deterministic visual state of the production progress dialog."""

    name: str
    size: tuple[int, int]
    update: SyncProgressUpdate
    expected_phase: str
    expected_summary: str
    outcome: str = "active"
    animation_none: bool = False


SCENARIOS = (
    Scenario(
        name="01-active-applying-100x30",
        size=NORMAL_SIZE,
        update=SyncProgressUpdate(
            phase=SyncPhase.APPLYING,
            completed=3,
            total=5,
            item="JPT-4",
            summary="Sending local changes to Jira",
        ),
        expected_phase="Applying changes",
        expected_summary="Sending local changes to Jira",
    ),
    Scenario(
        name="02-active-applying-80x24",
        size=NARROW_SIZE,
        update=SyncProgressUpdate(
            phase=SyncPhase.APPLYING,
            completed=3,
            total=5,
            item="JPT-4",
            summary="Sending local changes to Jira",
        ),
        expected_phase="Applying changes",
        expected_summary="Sending local changes to Jira",
    ),
    Scenario(
        name="03-complete-100x30",
        size=NORMAL_SIZE,
        update=SyncProgressUpdate(
            phase=SyncPhase.FINALIZING,
            completed=5,
            total=5,
            item="JPT-5",
            summary="Updating local Sync state",
        ),
        expected_phase="Complete",
        expected_summary="sent 2, wrote 3",
        outcome="complete",
    ),
    Scenario(
        name="04-held-100x30",
        size=NORMAL_SIZE,
        update=SyncProgressUpdate(
            phase=SyncPhase.FINALIZING,
            completed=5,
            total=5,
            item="JPT-5",
            summary="Updating local Sync state",
        ),
        expected_phase="Complete · Held locally",
        expected_summary="wrote 4, held 1",
        outcome="held",
    ),
    Scenario(
        name="05-failed-100x30",
        size=NORMAL_SIZE,
        update=SyncProgressUpdate(
            phase=SyncPhase.APPLYING,
            completed=2,
            total=5,
            item="JPT-2",
            summary="Sending local changes to Jira",
        ),
        expected_phase="Failed",
        expected_summary="Jira rejected JPT-2; no provider changes were retried.",
        outcome="failed",
    ),
    Scenario(
        name="06-active-reconciling-40x12",
        size=TINY_SIZE,
        update=SyncProgressUpdate(
            phase=SyncPhase.RECONCILING,
            completed=6,
            total=12,
            item="JPT-12345",
            summary="Writing cards to Markdown",
        ),
        expected_phase="Reconciling Markdown",
        expected_summary="Writing cards to Markdown",
        animation_none=True,
    ),
)


def _region(widget: Widget) -> list[int]:
    region = widget.region
    return [region.x, region.y, region.width, region.height]


def _label(screen: SyncProgressScreen, selector: str) -> str:
    return str(screen.query_one(selector, Label).render())


def _within(inner: Widget, outer: Widget) -> bool:
    return (
        inner.region.x >= outer.region.x
        and inner.region.y >= outer.region.y
        and inner.region.right <= outer.region.right
        and inner.region.bottom <= outer.region.bottom
    )


def _validate(
    app: KanbanApp,
    screen: SyncProgressScreen,
    scenario: Scenario,
) -> dict[str, Any]:
    dialog = screen.query_one("#sync-progress-dialog", Vertical)
    content = screen.query_one("#sync-progress-content", VerticalScroll)
    spinner = screen.query_one("#sync-progress-spinner", LoadingIndicator)
    close = screen.query_one("#sync-progress-close", Button)
    phase = _label(screen, "#sync-progress-phase")
    fraction = _label(screen, "#sync-progress-fraction")
    item = _label(screen, "#sync-progress-item")
    summary = _label(screen, "#sync-progress-summary")
    active = scenario.outcome == "active"

    if not _within(dialog, screen):
        raise RuntimeError(f"{scenario.name}: dialog is outside the terminal: {dialog.region!r}")
    if not _within(content, dialog):
        raise RuntimeError(f"{scenario.name}: progress body is outside the dialog")
    if not _within(close, dialog):
        raise RuntimeError(f"{scenario.name}: Close is clipped by the dialog")
    if content.max_scroll_x != 0:
        raise RuntimeError(f"{scenario.name}: horizontal overflow is {content.max_scroll_x}")
    for selector in (
        "#sync-progress-spinner",
        "#sync-progress-phase",
        "#sync-progress-fraction",
        "#sync-progress-item",
        "#sync-progress-summary",
    ):
        widget = screen.query_one(selector)
        if widget.region.right > content.content_region.right:
            raise RuntimeError(f"{scenario.name}: {selector} overflows the progress body")
    if phase != scenario.expected_phase:
        raise RuntimeError(f"{scenario.name}: expected phase {scenario.expected_phase!r}, got {phase!r}")
    if fraction != f"{scenario.update.completed} / {scenario.update.total}":
        raise RuntimeError(f"{scenario.name}: unexpected fraction {fraction!r}")
    if item != scenario.update.item:
        raise RuntimeError(f"{scenario.name}: unexpected current item {item!r}")
    if summary != scenario.expected_summary:
        raise RuntimeError(f"{scenario.name}: unexpected summary {summary!r}")
    if close.disabled is not active:
        raise RuntimeError(f"{scenario.name}: Close disabled={close.disabled}, expected {active}")
    if bool(spinner.display) is not active:
        raise RuntimeError(f"{scenario.name}: spinner display={spinner.display}, expected {active}")
    if (spinner.auto_refresh is not None) is not active:
        raise RuntimeError(
            f"{scenario.name}: spinner animation state does not match active={active}"
        )
    if scenario.animation_none and str(spinner.render()) != "Loading...":
        raise RuntimeError(f"{scenario.name}: reduced-motion loading fallback is missing")

    return {
        "terminal_cells": [app.size.width, app.size.height],
        "screen_region": _region(screen),
        "dialog_region": _region(dialog),
        "dialog_content_region": [
            dialog.content_region.x,
            dialog.content_region.y,
            dialog.content_region.width,
            dialog.content_region.height,
        ],
        "progress_body_region": _region(content),
        "progress_virtual_cells": [content.virtual_size.width, content.virtual_size.height],
        "progress_max_scroll_x": content.max_scroll_x,
        "progress_max_scroll_y": content.max_scroll_y,
        "vertical_scrollbar_visible": content.show_vertical_scrollbar,
        "spinner_region": _region(spinner),
        "spinner_displayed": bool(spinner.display),
        "spinner_auto_refresh": spinner.auto_refresh,
        "spinner_rendered": str(spinner.render()),
        "phase": phase,
        "phase_classes": sorted(screen.query_one("#sync-progress-phase").classes),
        "fraction": fraction,
        "item": item,
        "summary": summary,
        "close_region": _region(close),
        "close_disabled": close.disabled,
    }


def _settle_outcome(screen: SyncProgressScreen, outcome: str) -> None:
    if outcome == "complete":
        screen.finish_success(
            SyncReport(
                pushed=["JPT-1", "JPT-2"],
                written=["JPT-3", "JPT-4", "JPT-5"],
            )
        )
    elif outcome == "held":
        screen.finish_success(
            SyncReport(
                written=["JPT-1", "JPT-2", "JPT-3", "JPT-4"],
                held=["JPT-5.md"],
            )
        )
    elif outcome == "failed":
        screen.finish_error("Jira rejected JPT-2; no provider changes were retried.")


async def _capture(into: Path, scenario: Scenario) -> tuple[Path, dict[str, Any]]:
    app = KanbanApp(JsonBackend(), confirm_moves=False)
    if scenario.animation_none:
        app.animation_level = "none"
    async with app.run_test(size=scenario.size) as pilot:
        app.theme = "cyberpunk"
        await pilot.pause()
        screen = SyncProgressScreen("Jira", "JPT · Payments")
        await app.push_screen(screen)
        await pilot.pause()
        screen.update_progress(scenario.update)
        await pilot.pause()
        _settle_outcome(screen, scenario.outcome)
        await pilot.pause()
        metrics = _validate(app, screen, scenario)
        svg = into / f"{scenario.name}.svg"
        app.save_screenshot(str(svg.resolve()))
        return svg, metrics


def _rasterise(svg_path: Path) -> Path | None:
    """Rasterize the exact compositor SVG without reconstructing the UI."""
    png_path = svg_path.with_suffix(".png")
    try:
        import resvg_py
    except ImportError:
        pass
    else:
        png_path.write_bytes(resvg_py.svg_to_bytes(svg_path=str(svg_path)))
        return png_path

    try:
        import cairosvg
    except (ImportError, OSError):
        pass
    else:
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
        return png_path

    discovered = shutil.which("chrome") or shutil.which("google-chrome")
    candidates = (
        Path(discovered) if discovered else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    )
    renderer = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
    if renderer is None:
        return None

    view_box = svg_path.read_text(encoding="utf-8").split('viewBox="', 1)[1].split('"', 1)[0]
    _x, _y, width, height = (round(float(value)) for value in view_box.split())
    png_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="pykantui-sync-progress-shot-") as profile:
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
    return png_path if not result.returncode and png_path.is_file() else None


async def render(into: Path) -> tuple[Path, ...]:
    """Write all compositor captures and their verified cell metrics."""
    into.mkdir(parents=True, exist_ok=True)
    captures = [await _capture(into, scenario) for scenario in SCENARIOS]
    metrics_path = into / "metrics.json"
    metrics_path.write_text(
        json.dumps({svg.stem: metrics for svg, metrics in captures}, indent=2),
        encoding="utf-8",
    )
    return *(svg for svg, _metrics in captures), metrics_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, default=Path("artifacts/sync-progress"))
    arguments = parser.parse_args()
    generated = list(asyncio.run(render(arguments.into)))
    for svg in tuple(path for path in generated if path.suffix == ".svg"):
        if (png := _rasterise(svg)) is not None:
            generated.append(png)
    for path in generated:
        print(path.resolve())


if __name__ == "__main__":
    main()
