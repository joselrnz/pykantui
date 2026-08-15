"""Capture onboarding edge states from the real Textual compositor.

Run with::

    python tools/init_screenshots.py --into artifacts/init-empty-projects
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from pykantui.config import BoardConfig
from pykantui.pages.init_wizard import InitWizardApp

SIZE = (100, 38)


async def render(into: Path) -> Path:
    """Mount the zero-project journey and save its visible screen as SVG."""
    into.mkdir(parents=True, exist_ok=True)

    async def journey(wizard: InitWizardApp) -> Path | None:
        await wizard.wait_for_projects("Jira")
        return None

    with tempfile.TemporaryDirectory() as home:
        os.environ["PYKANTUI_HOME"] = home
        config = BoardConfig.load()
        config.theme = "cyberpunk"
        config.save()

        app = InitWizardApp(journey, intro_duration=0)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            target = into / "empty-projects.svg"
            app.save_screenshot(str(target.resolve()))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, required=True)
    arguments = parser.parse_args()
    print(asyncio.run(render(arguments.into)))


if __name__ == "__main__":
    main()
