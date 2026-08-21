"""Capture the real boxed ``kbn init`` onboarding stages from Textual.

The provider data is deterministic and offline, but every screen is mounted by
the production wizard and saved from Textual's compositor rather than drawn as
a mock-up.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from pykantui.commands.init_interactive import _choose_project, _choose_provider, _journey
from pykantui.commands.onboarding.credentials import choose_persistence, collect_credentials
from pykantui.commands.onboarding.models import (
    CredentialSetup,
    CredentialSource,
)
from pykantui.config import BoardConfig
from pykantui.pages.init_wizard import InitWizardApp, Journey
from pykantui.tracker import get
from pykantui.tracker.base import Provider
from pykantui.tracker.models import RemoteProject, RemoteUser
from pykantui.workspace import layout
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.models import SyncReport

SIZE = (100, 38)


async def _capture(
    into: Path,
    name: str,
    journey: Journey,
    *,
    ready_selector: str | None = None,
) -> Path:
    app = InitWizardApp(journey, intro_duration=0)
    # A compositor capture has an explicit test viewport. Do not let the
    # missed-SIGWINCH recovery timer replace it with the invoking shell's
    # dimensions while a longer journey is still running.
    with patch("pykantui.tui.terminal.current_terminal_size", return_value=None):
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            if ready_selector is not None:
                for _attempt in range(100):
                    if len(app.screen.query(ready_selector)):
                        break
                    await pilot.pause()
                else:
                    raise RuntimeError(f"{name} never rendered {ready_selector}")
                # The node can exist one compositor cycle before its final
                # dimensions and focus style have settled.
                await pilot.pause()
            target = into / f"{name}.svg"
            app.save_screenshot(str(target.resolve()))
    png = _rasterise(target)
    return png or target


def _rasterise(svg_path: Path) -> Path | None:
    """Convert a genuine Textual SVG capture to PNG when a renderer exists."""

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
        return None
    try:
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
    except OSError:
        return None
    return png_path


async def render(into: Path) -> list[Path]:
    """Mount and capture provider, authentication, persistence, and project boxes."""

    into.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    with tempfile.TemporaryDirectory() as home:
        environment = {
            "PYKANTUI_HOME": home,
            "ASANA_TOKEN": "",
            "ASANA_ACCESS_TOKEN": "",
            "GITHUB_TOKEN": "",
            "GH_TOKEN": "",
            "GITHUB_API_URL": "",
        }
        with patch.dict(os.environ, environment, clear=False):
            config = BoardConfig.load()
            config.theme = "cyberpunk"
            config.save()

            async def provider_journey(wizard: InitWizardApp) -> Path | None:
                await _choose_provider(wizard)
                return None

            rendered.append(await _capture(into, "01-provider", provider_journey))

            async def authentication_journey(wizard: InitWizardApp) -> Path | None:
                await collect_credentials(
                    wizard,
                    "asana",
                    {},
                    open_url=lambda _url: False,
                )
                return None

            rendered.append(await _capture(into, "02-authentication", authentication_journey))

            async def key_journey(wizard: InitWizardApp) -> Path | None:
                await wizard.prompt(
                    "Personal access token",
                    note=(
                        "Environment: GITHUB_TOKEN or GH_TOKEN\n"
                        "The value is masked and never written to the workspace."
                    ),
                    placeholder="paste token",
                    secret=True,
                )
                return None

            rendered.append(await _capture(into, "03-key-entry", key_journey))

            async def persistence_journey(wizard: InitWizardApp) -> Path | None:
                setup = CredentialSetup(
                    config={"base_url": "https://api.github.com", "repo": "owner/repo"},
                    _secrets={"token": "never-rendered"},
                    sources={"token": CredentialSource.ENTERED},
                )
                await choose_persistence(wizard, "github", setup)
                return None

            rendered.append(await _capture(into, "04-persistence", persistence_journey))

            async def project_journey(wizard: InitWizardApp) -> Path | None:
                provider = Mock()
                provider.spec = get("github").spec
                projects = [
                    RemoteProject(
                        project_id="acme-platform/pykantui",
                        key="pykantui",
                        name="pykantui",
                        description="Local-first terminal boards and provider sync.",
                    ),
                    RemoteProject(
                        project_id="acme-platform/automation-lab",
                        key="automation-lab",
                        name="automation-lab",
                        description="Workflow experiments and task automation.",
                    ),
                    RemoteProject(
                        project_id="acme-platform/terminal-design",
                        key="terminal-design",
                        name="terminal-design",
                        description="Terminal interface research and glyph tests.",
                    ),
                    RemoteProject(
                        project_id="acme-platform/local-first-notes",
                        key="local-first-notes",
                        name="local-first-notes",
                        description="Versioned Markdown notes and offline workflows.",
                    ),
                    RemoteProject(
                        project_id="open-source/pykantui",
                        key="pykantui",
                        name="pykantui",
                        owner="open-source",
                        description="A similarly named repository in another owner scope.",
                    ),
                ]
                wizard.done("5 repositories available")
                wizard.done("Connected as alex")
                await _choose_project(wizard, cast(Provider, provider), projects, {})
                return None

            rendered.append(await _capture(into, "05-repositories", project_journey))

            async def one_project_journey(wizard: InitWizardApp) -> Path | None:
                provider = Mock()
                provider.spec = get("linear").spec
                team = RemoteProject(
                    project_id="team-01H8T7K",
                    key="ENG",
                    name="Engineering",
                    description="The only Linear team accessible to this token.",
                )
                wizard.done("1 team available")
                wizard.done("Connected as alex")
                await _choose_project(wizard, cast(Provider, provider), [team], {})
                return None

            rendered.append(await _capture(into, "06-confirm-team", one_project_journey))

            async def folder_journey(wizard: InitWizardApp) -> Path | None:
                await wizard.choose_folder(Path.cwd(), title="Where should pykantui live?")
                return None

            rendered.append(await _capture(into, "07-folder", folder_journey))

            # Drive the production journey all the way through its blocking
            # success modal. Provider discovery and sync are deterministic
            # stand-ins, while _create remains real so project.json and the
            # credential/workspace separation are exercised exactly as they
            # are by ``kbn init``.
            project = RemoteProject(
                project_id="acme-platform/pykantui",
                key="pykantui",
                name="pykantui",
                description="Local-first terminal boards and provider sync.",
            )
            user = RemoteUser(account_id="offline-alex", display_name="alex")
            workspace = Path(home) / "workspace" / "pykantui"
            secret = "never-rendered"
            completion_args = argparse.Namespace(
                provider="github",
                path=workspace,
                name="pykantui",
                browse=True,
                do_sync=True,
                use_git=False,
                columns=ColumnStyle.SLUG.value,
                open_board=True,
                f_repo=project.project_id,
                f_token=secret,
            )

            async def completion_journey(wizard: InitWizardApp) -> Path:
                return await _journey(completion_args, wizard)

            report = SyncReport(written=["GH-101", "GH-102", "GH-103"])
            with (
                patch(
                    "pykantui.commands.init_interactive.connect_and_discover",
                    new=AsyncMock(return_value=([project], user)),
                ),
                patch(
                    "pykantui.commands.init_interactive._choose_project",
                    new=AsyncMock(return_value=project),
                ),
                patch("pykantui.commands.init_interactive.sync_module.sync", return_value=report),
            ):
                rendered.append(
                    await _capture(
                        into,
                        "08-complete",
                        completion_journey,
                        ready_selector="#wizard-complete-dialog",
                    )
                )

            project_path = layout.project_file(workspace)
            if not project_path.is_file():
                raise RuntimeError("completion capture did not create project.json")
            if secret in project_path.read_text(encoding="utf-8"):
                raise RuntimeError("completion capture exposed its credential in project.json")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, required=True)
    arguments = parser.parse_args()
    for path in asyncio.run(render(arguments.into)):
        print(path)


if __name__ == "__main__":
    main()
