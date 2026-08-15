"""One continuous full-screen home for the interactive init journey."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path

from rich.cells import cell_len
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Footer, Header, Input, Label, LoadingIndicator, Static

from pykantui.cli.presentation import render_loader_intro
from pykantui.i18n import translate as _
from pykantui.pages.chooser import Choice, Chooser
from pykantui.pages.folder import FolderPicker
from pykantui.pages.navigation import NavigationAction
from pykantui.pages.styling import DIALOG_CSS, Themed
from pykantui.tracker import ProviderError
from pykantui.tui.terminal import TerminalResizeMixin

INTRO_DURATION_SECONDS = 5.0
INTRO_FRAME_SECONDS = 0.08
INTRO_SIGNAL_FRAMES = 8
INTRO_ASSEMBLY_FRAMES = 20
INTRO_DECODE_FRAMES = 32
INTRO_PROGRESS_CELLS = 30
INTRO_PROGRESS_SEGMENTS = INTRO_PROGRESS_CELLS // 2
INTRO_SYNC_CELLS = 13
_FLIP_GLYPHS = (".", ":", "/", "\\", "|", "_", "-")
_STAGE_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class IntroPhase(StrEnum):
    """Stable, user-visible phases of the startup build sequence."""

    SIGNAL = "Acquiring signal"
    ASSEMBLY = "Assembling glyph matrix"
    DECODE = "Decoding PYKANTUI"
    ONLINE = "Local-first board online"


async def _wait_intro(duration: float) -> None:
    """Keep the splash visible while Textual renders the build sequence."""
    await asyncio.sleep(duration)


async def _wait_intro_or_skip(duration: float, skipped: asyncio.Event) -> None:
    """Wait for the intro timeout or a user skip, whichever happens first."""
    delay = asyncio.create_task(_wait_intro(duration))
    skip = asyncio.create_task(skipped.wait())
    _, pending = await asyncio.wait((delay, skip), return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(delay, skip, return_exceptions=True)


class WizardCancelled(Exception):
    """The user deliberately left the onboarding journey."""


class WizardBack(Exception):
    """The user requested the preceding onboarding step."""


class WizardPrompt(ModalScreen[str | NavigationAction | None]):
    """Ask for one provider value without dropping back to a shell prompt."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+b", "back", "back", show=False),
        Binding("ctrl+c", "cancel", "cancel"),
    ]

    DEFAULT_CSS = (
        DIALOG_CSS
        + """
    WizardPrompt { align: center middle; }
    WizardPrompt #wizard-prompt-dialog { height: auto; }
    WizardPrompt #wizard-prompt-note { color: $text-muted; height: auto; }
    WizardPrompt #wizard-input {
        height: 3;
        margin: 1 0;
        border: round $primary-lighten-3;
        background: $background;
    }
    WizardPrompt #wizard-input:focus { border: round $accent-lighten-3; }
    WizardPrompt #wizard-prompt-ok,
    WizardPrompt #wizard-prompt-ok.-primary {
        border: round $accent;
        background: transparent;
        color: $accent;
        text-style: bold;
    }
    """
    )

    def __init__(self, title: str, *, note: str = "", placeholder: str = "", secret: bool = False) -> None:
        super().__init__()
        self._title = title
        self._note = note
        self._placeholder = placeholder
        self._secret = secret

    def compose(self) -> ComposeResult:
        back_label = _("Back")
        yield Header()
        with Vertical(id="wizard-prompt-dialog", classes="pk-dialog"):
            yield Label(self._title, classes="pk-title")
            yield Static(self._note, id="wizard-prompt-note")
            yield Input(placeholder=self._placeholder, password=self._secret, id="wizard-input")
            with Horizontal(classes="pk-buttons"):
                yield Button(f"← {back_label}", id="wizard-prompt-back")
                yield Button(_("Cancel"), id="wizard-prompt-cancel")
                yield Button(_("Continue"), variant="primary", id="wizard-prompt-ok")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#wizard-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "wizard-prompt-ok":
            self._submit()
        elif event.button.id == "wizard-prompt-back":
            self.action_back()
        else:
            self.action_cancel()

    def _submit(self) -> None:
        value = self.query_one("#wizard-input", Input).value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_back(self) -> None:
        self.dismiss(NavigationAction.BACK)


class WizardMessage(ModalScreen[None]):
    """Keep an init failure in the TUI until the user acknowledges it."""

    BINDINGS = [Binding("enter,escape", "close", "close"), Binding("ctrl+c", "close", "close")]

    DEFAULT_CSS = (
        DIALOG_CSS
        + """
    WizardMessage { align: center middle; }
    WizardMessage #wizard-message-dialog { height: auto; }
    WizardMessage #wizard-message-title { color: $error; text-style: bold; }
    WizardMessage #wizard-message-body { height: auto; margin: 1 0; color: $text; }
    WizardMessage #wizard-message-close {
        border: round $accent;
        background: transparent;
        color: $accent;
        text-style: bold;
    }
    """
    )

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="wizard-message-dialog", classes="pk-dialog"):
            yield Static(self._title, id="wizard-message-title")
            yield Static(self._message, id="wizard-message-body")
            with Horizontal(classes="pk-buttons"):
                yield Button(_("Close"), id="wizard-message-close", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#wizard-message-close", Button).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()


class WizardComplete(ModalScreen[None]):
    """Hold the successful result until the user opens the board or finishes."""

    BINDINGS = [Binding("enter", "continue", "continue", show=False)]

    DEFAULT_CSS = (
        DIALOG_CSS
        + """
    WizardComplete { align: center middle; }
    WizardComplete #wizard-complete-dialog {
        height: auto;
        max-height: 26;
    }
    WizardComplete #wizard-complete-title {
        color: $success;
        text-style: bold;
        content-align: center middle;
    }
    WizardComplete #wizard-complete-summary {
        height: auto;
        margin: 1 0;
        padding: 1 2;
        border: round $border-blurred;
        background: $background 60%;
    }
    WizardComplete #wizard-complete-open,
    WizardComplete #wizard-complete-open.-primary {
        border: round $accent;
        background: transparent;
        color: $accent;
        text-style: bold;
    }
    """
    )

    def __init__(
        self,
        *,
        provider: str,
        project: str,
        scope_label: str = "Project",
        workspace: Path,
        sync_summary: str,
        open_board: bool,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._project = project
        self._scope_label = scope_label
        self._workspace = workspace
        self._sync_summary = sync_summary
        self._open_board = open_board

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="wizard-complete-dialog", classes="pk-dialog"):
            yield Static(f"✓  {_('Setup complete')}", id="wizard-complete-title")
            yield Static(self._render_summary(), id="wizard-complete-summary")
            with Horizontal(classes="pk-buttons"):
                label = _("Open board") if self._open_board else _("Finish")
                yield Button(label, id="wizard-complete-open", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#wizard-complete-open", Button).focus()

    def action_continue(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_continue()

    def _render_summary(self) -> Text:
        summary = Text()
        rows = (
            (_("Provider"), self._provider),
            (self._scope_label, self._project),
            (_("Workspace"), str(self._workspace)),
            (_("Initial sync"), self._sync_summary),
        )
        label_width = max(cell_len(label) for label, _value in rows) + 2
        for label, value in rows:
            summary.append(label, style="#6FB2FF bold")
            summary.append(" " * (label_width - cell_len(label)))
            summary.append(f"{value}\n", style="#C9D1D9")
        summary.rstrip()
        return summary


class WizardEmptyProjects(ModalScreen[bool | NavigationAction]):
    """Pause onboarding when a connected account has no visible projects."""

    BINDINGS = [
        Binding("enter", "retry", "check again"),
        Binding("ctrl+b", "back", "back", show=False),
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+c", "cancel", "cancel"),
    ]

    DEFAULT_CSS = (
        DIALOG_CSS
        + """
    WizardEmptyProjects { align: center middle; }
    WizardEmptyProjects #wizard-empty-dialog { height: auto; }
    WizardEmptyProjects #wizard-empty-title {
        color: $text;
        text-style: bold;
    }
    WizardEmptyProjects #wizard-empty-body {
        height: auto;
        margin: 1 0;
        color: $text-muted;
    }
    WizardEmptyProjects #wizard-empty-retry,
    WizardEmptyProjects #wizard-empty-retry.-primary {
        border: round $accent;
        background: transparent;
        color: $accent;
        text-style: bold;
    }
    """
    )

    def __init__(
        self,
        provider_label: str,
        *,
        scope_singular: str = "project",
        scope_plural: str = "projects",
    ) -> None:
        super().__init__()
        self._provider_label = provider_label
        self._scope_singular = scope_singular
        self._scope_plural = scope_plural

    def compose(self) -> ComposeResult:
        back_label = _("Back")
        yield Header()
        with Vertical(id="wizard-empty-dialog", classes="pk-dialog"):
            yield Static(
                f"No {self._provider_label} {self._scope_plural} found",
                id="wizard-empty-title",
            )
            yield Static(
                f"Create a {self._scope_singular} in {self._provider_label}, then return here "
                "and check again. No local files have been created.",
                id="wizard-empty-body",
            )
            with Horizontal(classes="pk-buttons"):
                yield Button(f"← {back_label}", id="wizard-empty-back")
                yield Button(_("Cancel"), id="wizard-empty-cancel")
                yield Button(_("Check again"), id="wizard-empty-retry", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#wizard-empty-retry", Button).focus()

    def action_retry(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_back(self) -> None:
        self.dismiss(NavigationAction.BACK)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "wizard-empty-retry":
            self.action_retry()
        elif event.button.id == "wizard-empty-back":
            self.action_back()
        else:
            self.action_cancel()


Journey = Callable[["InitWizardApp"], Awaitable[Path | None]]


class InitWizardApp(Themed, TerminalResizeMixin, App[Path | None]):
    """Runs every init screen and loading phase under one alternate screen."""

    TITLE = "pykantui"
    SUB_TITLE = "setup"
    BINDINGS = [Binding("escape", "skip_intro", "skip intro", show=False)]
    CSS = (
        DIALOG_CSS
        + Chooser.DEFAULT_CSS
        + FolderPicker.DEFAULT_CSS
        + WizardPrompt.DEFAULT_CSS
        + WizardMessage.DEFAULT_CSS
        + WizardComplete.DEFAULT_CSS
        + WizardEmptyProjects.DEFAULT_CSS
        + """
        #wizard-topbar {
            width: 100%;
            height: 1;
            background: #0D1117;
        }
        #wizard-mark {
            width: 3;
            color: #00C8FF;
            content-align: center middle;
        }
        #wizard-title {
            width: 1fr;
            color: $text-muted;
            content-align: center middle;
        }
        #wizard-close {
            width: 3;
            min-width: 3;
            height: 1;
            padding: 0;
            border: none;
            background: $background;
            color: #00C8FF;
        }
        #wizard-close:hover,
        #wizard-close:focus {
            background: #11161D;
            color: #7BFFFF;
            text-style: bold;
        }
        #wizard-shell {
            width: 100%;
            height: 1fr;
            padding: 3 3 1 3;
            background: $background;
        }
        #wizard-logo {
            width: 100%;
            height: auto;
            color: $accent;
            content-align: center top;
        }
        #wizard-sync,
        #wizard-stage {
            width: 100%;
            height: 1;
            color: $text-accent;
            text-style: bold;
            content-align: center middle;
        }
        #wizard-sync { margin-top: 1; }
        #wizard-stage { margin-top: 1; }
        #wizard-progress {
            width: 100%;
            height: 1;
            margin: 1 0;
            content-align: center middle;
        }
        #wizard-spinner { height: 1; color: $accent; }
        #wizard-log {
            width: 100%;
            height: 1fr;
            max-height: 9;
            padding: 0 2;
            color: $text-muted;
        }
        """
    )

    def __init__(
        self,
        journey: Journey,
        *,
        intro_duration: float = INTRO_DURATION_SECONDS,
        acknowledge_completion: bool = True,
    ) -> None:
        super().__init__()
        self._journey = journey
        self._intro_duration = max(0.0, intro_duration)
        self._acknowledge_completion = acknowledge_completion
        self._intro_frame = 0
        self._intro_width = 100
        self._intro_timer: Timer | None = None
        self._intro_skipped = asyncio.Event()
        self._intro_finished = False
        self._steps: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="wizard-topbar"):
            yield Static("◌", id="wizard-mark")
            yield Static(_("pykantui - setup"), id="wizard-title")
            yield Button("x", id="wizard-close")
        with Vertical(id="wizard-shell"):
            yield Static(_styled_intro(100), id="wizard-logo")
            yield Static(_sync_rail(0), id="wizard-sync")
            yield Static(_styled_stage(0), id="wizard-stage")
            yield Static(_progress_rail(0), id="wizard-progress")
            yield LoadingIndicator(id="wizard-spinner")
            yield Static("", id="wizard-log")

    def on_mount(self) -> None:
        self.apply_theme()
        self._start_terminal_resize_monitor()
        self._intro_width = max(40, self.size.width - 6)
        if self._intro_duration:
            self._render_intro_frame(0)
            self._intro_timer = self.set_interval(INTRO_FRAME_SECONDS, self._animate_intro)
        else:
            self._finish_intro()
        self.query_one("#wizard-spinner", LoadingIndicator).display = False
        self.run_worker(self._drive(), exclusive=True)

    async def _drive(self) -> None:
        if self._intro_duration:
            await _wait_intro_or_skip(self._intro_duration, self._intro_skipped)
        self._finish_intro()
        try:
            result = await self._journey(self)
        except WizardCancelled:
            self.exit(None)
            return
        except (ProviderError, OSError, ValueError) as error:
            self.done(_("Setup stopped"))
            await self.push_screen_wait(WizardMessage(_("Could not finish setup"), str(error)))
            self.exit(None)
            return
        except Exception:  # noqa: BLE001 - the TUI boundary must not expose the shell
            self.done(_("Setup stopped"))
            await self.push_screen_wait(
                WizardMessage(
                    _("Could not finish setup"),
                    _(
                        "An unexpected error stopped setup. The last completed step is shown "
                        "behind this message. Close it, then run `kbn init` again."
                    ),
                )
            )
            self.exit(None)
            return
        self.exit(result)

    async def choose(
        self,
        choices: list[Choice],
        *,
        title: str,
        filter_hint: str = "type to filter",
        allow_back: bool = True,
    ) -> str:
        if not choices:
            raise WizardCancelled
        selected = await self.push_screen_wait(
            Chooser(choices, title=title, filter_hint=filter_hint, allow_back=allow_back)
        )
        if selected is NavigationAction.BACK:
            raise WizardBack
        if selected is None:
            raise WizardCancelled
        return selected

    async def choose_folder(self, start: Path, *, title: str) -> Path:
        selected = await self.push_screen_wait(FolderPicker(start, title=title, allow_back=True))
        if selected is NavigationAction.BACK:
            raise WizardBack
        if selected is None:
            raise WizardCancelled
        return selected

    async def wait_for_projects(
        self,
        provider_label: str,
        *,
        scope_singular: str = "project",
        scope_plural: str = "projects",
    ) -> None:
        """Keep an empty provider account inside setup until retry or cancel."""
        retry = await self.push_screen_wait(
            WizardEmptyProjects(
                provider_label,
                scope_singular=scope_singular,
                scope_plural=scope_plural,
            )
        )
        if retry is NavigationAction.BACK:
            raise WizardBack
        if not retry:
            raise WizardCancelled

    async def prompt(
        self,
        title: str,
        *,
        note: str = "",
        placeholder: str = "",
        secret: bool = False,
    ) -> str:
        value = await self.push_screen_wait(WizardPrompt(title, note=note, placeholder=placeholder, secret=secret))
        if value is NavigationAction.BACK:
            raise WizardBack
        if value is None:
            raise WizardCancelled
        return value

    async def finish(
        self,
        *,
        provider: str,
        project: str,
        scope_label: str = "Project",
        workspace: Path,
        sync_summary: str,
        open_board: bool,
    ) -> None:
        """Show a stable, secret-free setup result before leaving the wizard."""
        if not self._acknowledge_completion:
            return
        await self.push_screen_wait(
            WizardComplete(
                provider=provider,
                project=project,
                scope_label=scope_label,
                workspace=workspace,
                sync_summary=sync_summary,
                open_board=open_board,
            )
        )

    def loading(self, message: str) -> None:
        self._steps.append(("loading", message))
        self._show_steps(message, spinning=True)

    def done(self, message: str) -> None:
        if self._steps and self._steps[-1][0] == "loading":
            self._steps[-1] = ("done", message)
        else:
            self._steps.append(("done", message))
        self._show_steps(message, spinning=False)

    def note(self, message: str) -> None:
        self._steps.append(("note", message))
        self._show_steps(message, spinning=False)

    def _show_steps(self, stage: str, *, spinning: bool) -> None:
        self.query_one("#wizard-stage", Static).update(stage)
        self.query_one("#wizard-spinner", LoadingIndicator).display = spinning
        output = Text()
        for state, message in self._steps[-7:]:
            marker, style = {
                "loading": ("◐", "#00C8FF"),
                "done": ("✓", "#00C8FF"),
                "note": ("·", "#6FB2FF"),
            }[state]
            output.append(f" {marker} ", style=style)
            output.append(f"{message}\n")
        self.query_one("#wizard-log", Static).update(output)

    def _animate_intro(self) -> None:
        """Build, decode, and sweep the logo without changing its geometry."""
        self._intro_frame += 1
        self._render_intro_frame(self._intro_frame)

    def _render_intro_frame(self, frame: int) -> None:
        """Render every synchronized element of one intro frame."""
        self.query_one("#wizard-logo", Static).update(_styled_intro(self._intro_width, frame=frame))
        self.query_one("#wizard-sync", Static).update(_sync_rail(frame))
        self.query_one("#wizard-stage", Static).update(_styled_stage(frame))
        self.query_one("#wizard-progress", Static).update(_progress_rail(frame))

    def _finish_intro(self) -> None:
        """Settle the animation into its stable ready state exactly once."""
        if self._intro_finished:
            return
        if self._intro_timer is not None:
            self._intro_timer.pause()
        self._intro_finished = True
        self.query_one("#wizard-logo", Static).update(_styled_intro(self._intro_width))
        self.query_one("#wizard-sync", Static).update(_sync_rail(INTRO_DECODE_FRAMES))
        self.query_one("#wizard-stage", Static).update(
            _styled_stage(INTRO_DECODE_FRAMES, label="Starting local-first setup")
        )
        self.query_one("#wizard-progress", Static).update(_progress_rail(INTRO_DECODE_FRAMES))

    def action_skip_intro(self) -> None:
        """Skip the splash without cancelling the setup journey."""
        if not self._intro_finished:
            self._intro_skipped.set()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Allow the title-bar close control to leave setup."""
        if event.button.id == "wizard-close":
            event.stop()
            self.exit(None)


def _styled_intro(width: int, *, frame: int | None = None) -> Text:
    logo, tagline = render_loader_intro(width=width).rsplit("\n", maxsplit=1)
    animated_logo = _animate_logo_text(logo, frame) if frame is not None else logo
    rendered = Text(no_wrap=True, justify="center")
    base_style = (
        {
            IntroPhase.SIGNAL: "#04586E",
            IntroPhase.ASSEMBLY: "#08789A",
            IntroPhase.DECODE: "#00A7D6",
            IntroPhase.ONLINE: "#00C8FF",
        }[_intro_phase(frame)]
        if frame is not None
        else "#00C8FF"
    )
    rendered.append(animated_logo, style=base_style)
    for index, character in enumerate(animated_logo):
        if character == "▒":
            rendered.stylize("#08789A", index, index + 1)
        elif character == "░":
            rendered.stylize("#04586E", index, index + 1)
    if frame is not None:
        # Highlight a whole terminal row so the result reads as one horizontal
        # scanline rather than a cloud of unrelated bright characters.
        lines = animated_logo.splitlines()
        scan_row = (frame // 2) % len(lines)
        start = sum(len(line) + 1 for line in lines[:scan_row])
        rendered.stylize("bold underline #7BFFFF", start, start + len(lines[scan_row]))
    tagline_style = "bold #7BFFFF" if frame is not None and frame % 10 < 3 else "#6FB2FF"
    rendered.append(f"\n{tagline}", style=tagline_style)
    return rendered


def _animate_logo_text(logo: str, frame: int) -> str:
    """Build and decode the logo, then offset an occasional scanline.

    Every replacement is one ASCII cell and every shifted row keeps its exact
    length. The animation can look unstable without making the layout unstable.
    """
    if frame < INTRO_SIGNAL_FRAMES:
        return _acquire_signal(logo, frame)
    if frame < INTRO_ASSEMBLY_FRAMES:
        return _assemble_strokes(logo, frame - INTRO_SIGNAL_FRAMES)
    if frame < INTRO_DECODE_FRAMES:
        return _decode_strokes(logo, frame - INTRO_ASSEMBLY_FRAMES)

    phase = frame - INTRO_DECODE_FRAMES
    glitch_step = phase % 17
    if glitch_step not in (0, 1):
        return logo

    lines = logo.split("\n")
    row = ((phase // 17) * 5 + glitch_step) % len(lines)
    line = lines[row]
    if line:
        lines[row] = (" " + line[:-1]) if glitch_step == 0 else (line[1:] + " ")
    return "\n".join(lines)


def _acquire_signal(logo: str, frame: int) -> str:
    """Pulse a sparse, deterministic constellation over the future strokes."""
    characters = list(logo)
    density = 5 + frame * 2
    for index, character in enumerate(characters):
        if character.isspace():
            continue
        signal = (index * 37 + index // 11 * 17) % 100
        characters[index] = _FLIP_GLYPHS[(index + frame) % len(_FLIP_GLYPHS)] if signal < density else " "
    return "".join(characters)


def _assemble_strokes(logo: str, frame: int) -> str:
    """Reveal complete strokes from left to right like a terminal sweep."""
    lines = logo.split("\n")
    progress = (frame + 1) / (INTRO_ASSEMBLY_FRAMES - INTRO_SIGNAL_FRAMES)
    assembled: list[str] = []
    for row, line in enumerate(lines):
        cells = list(line)
        for column, character in enumerate(cells):
            if character.isspace():
                continue
            position = column / max(1, len(line) - 1)
            stagger = (row % 3) * 0.035
            if position > min(1.0, progress + stagger):
                cells[column] = " "
        assembled.append("".join(cells))
    return "\n".join(assembled)


def _decode_strokes(logo: str, frame: int) -> str:
    """Flip every occupied cell until its final logo character locks in."""
    characters = list(logo)
    duration = INTRO_DECODE_FRAMES - INTRO_ASSEMBLY_FRAMES
    for index, character in enumerate(characters):
        if character.isspace():
            continue
        reveal_at = (index * 11 + index // 7) % duration
        if frame < reveal_at:
            characters[index] = _FLIP_GLYPHS[(index + frame) % len(_FLIP_GLYPHS)]
    return "".join(characters)


def _intro_phase(frame: int | None) -> IntroPhase:
    """Return the animation phase for a frame number."""
    if frame is None or frame >= INTRO_DECODE_FRAMES:
        return IntroPhase.ONLINE
    if frame < INTRO_SIGNAL_FRAMES:
        return IntroPhase.SIGNAL
    if frame < INTRO_ASSEMBLY_FRAMES:
        return IntroPhase.ASSEMBLY
    return IntroPhase.DECODE


def _intro_stage(frame: int) -> str:
    """Describe the visible build phase beneath the logo."""
    return _intro_phase(frame).value


def _styled_stage(frame: int, *, label: str | None = None) -> Text:
    """Render the current build phase with a stable, animated status marker."""
    ready = frame >= INTRO_DECODE_FRAMES
    marker = "✓" if ready else _STAGE_SPINNER[frame % len(_STAGE_SPINNER)]
    marker_style = "bold #98C379" if ready else "bold #7BFFFF"

    rendered = Text(no_wrap=True)
    rendered.append(marker, style=marker_style)
    rendered.append(f" {label or _intro_stage(frame)}", style="bold #6FB2FF")
    return rendered


def _sync_rail(frame: int) -> Text:
    """Render a fixed-width local-to-provider handshake with a moving pulse."""
    ready = frame >= INTRO_DECODE_FRAMES
    track = ["─"] * INTRO_SYNC_CELLS
    pulse = None if ready else frame % INTRO_SYNC_CELLS
    if pulse is not None:
        track[pulse] = "█"

    rendered = Text(no_wrap=True)
    rendered.append("LOCAL", style="#6FB2FF")
    rendered.append("  ")
    for index, cell in enumerate(track):
        style = "bold #7BFFFF" if index == pulse else "#08789A"
        rendered.append(cell, style=style)
    rendered.append("  PROVIDER", style="#6FB2FF")
    rendered.append("  ✓ READY" if ready else "         ", style="#98C379" if ready else "#08789A")
    return rendered


def _progress_rail(frame: int) -> Text:
    """Render a fixed-width progress rail with a comet trail and ready shimmer."""
    ready = frame >= INTRO_DECODE_FRAMES
    progress = min(frame + 1, INTRO_DECODE_FRAMES) / INTRO_DECODE_FRAMES
    filled = INTRO_PROGRESS_SEGMENTS if ready else max(1, round(progress * INTRO_PROGRESS_SEGMENTS))
    percent = 100 if ready else round(progress * 100)

    rendered = Text(no_wrap=True)
    rendered.append("▏", style="#08789A")
    shimmer = frame % INTRO_PROGRESS_SEGMENTS
    for index in range(INTRO_PROGRESS_SEGMENTS):
        if ready:
            distance = (index - shimmer) % INTRO_PROGRESS_SEGMENTS
            style = "bold #7BFFFF" if distance == 0 else "#00C8FF"
            rendered.append("█ ", style=style)
        elif index >= filled:
            rendered.append("░ ", style="#04586E")
        elif index == filled - 1:
            rendered.append("█ ", style="bold #7BFFFF")
        elif index == filled - 2:
            rendered.append("▓ ", style="#00C8FF")
        elif index == filled - 3:
            rendered.append("▒ ", style="#6FB2FF")
        else:
            rendered.append("█ ", style="#00C8FF")
    rendered.append("▏", style="#08789A")
    rendered.append(f" {percent:>3}%", style="bold #6FB2FF")
    return rendered
