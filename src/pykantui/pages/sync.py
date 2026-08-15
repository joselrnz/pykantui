"""Review the provider writes a Sync is about to perform."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, LoadingIndicator, Select

from pykantui.i18n import ntranslate as ngettext
from pykantui.i18n import translate as _
from pykantui.workspace.models import ConflictResolution, SyncReport
from pykantui.workspace.progress import SyncPhase, SyncProgressUpdate
from pykantui.workspace.sync import SyncPlan

_FIELD_LABELS = {
    "title": "Summary",
    "body": "Description",
    "column_id": "Status",
    "assignee": "Assignee",
    "labels": "Labels",
    "components": "Components",
    "due_date": "Due Date",
    "priority": "Priority",
    "issue_type": "Type",
}


class SyncChoice(StrEnum):
    """A reviewed synchronization decision returned to the controller."""

    CANCEL = "cancel"
    PULL = "pull"
    SEND = "send"
    USE_PROVIDER = "use-provider"
    FORCE = "force"


@dataclass(frozen=True, slots=True)
class SyncDecision:
    """One dialog action plus explicit decisions for conflicting fields."""

    choice: SyncChoice
    conflicts: dict[str, dict[str, ConflictResolution]] = field(default_factory=dict)


class SyncConfirmScreen(ModalScreen[SyncDecision]):
    """Return ``send``, ``pull`` or ``cancel`` without doing the work itself."""

    BINDINGS = [
        Binding("escape,n", "cancel", "Cancel"),
        Binding("p", "pull", "Pull only"),
        Binding("y", "send", "Send ready changes"),
    ]

    def __init__(self, provider: str, project: str, plan: SyncPlan) -> None:
        super().__init__()
        self.provider_name = _one_line(provider)
        self.project_name = _one_line(project)
        self.plan = plan
        self._conflict_items = {
            item.previous.issue_id: item for item in self.plan.conflicts()
        }
        self._conflict_widgets: dict[str, tuple[str, str]] = {}
        for item_index, item in enumerate(self.plan.conflicts()):
            for field_name in item.conflicting_fields():
                self._conflict_widgets[f"sync-conflict-{item_index}-{field_name.replace('_', '-')}"] = (
                    item.previous.issue_id,
                    field_name,
                )

    def compose(self) -> ComposeResult:
        with Vertical(id="sync-dialog"):
            yield Label(Text(_("SYNC PREVIEW")), id="sync-heading", markup=False)
            yield Label(
                Text(f"{self.provider_name} · {self.project_name}"),
                id="sync-destination",
                markup=False,
            )
            with VerticalScroll(id="sync-content", can_focus=True):
                sendable = self.plan.describe_sendable()
                if sendable:
                    yield Label(
                        Text(_multiline(sendable)),
                        id="sync-sendable",
                        markup=False,
                    )
                blocked = self.plan.describe_blocked()
                if blocked:
                    yield Label(
                        Text(_multiline(blocked)),
                        id="sync-blocked",
                        markup=False,
                    )
                yield Label(Text(_("LOCAL ONLY")), classes="sync-section", markup=False)
                yield Label(
                    Text(
                        _(
                            "• Private Markdown notes\n"
                            "• Local Git history\n"
                            "• Credentials and cache files"
                        )
                    ),
                    id="sync-local",
                    markup=False,
                )
                if self.plan.conflicts():
                    yield Label(
                        Text(
                            _(
                                "Choose provider or local for each conflicting field. "
                                "Undecided cards stay local."
                            )
                        ),
                        id="sync-warning",
                        markup=False,
                    )
                    yield Label(
                        Text(_("RESOLVE CONFLICTS")),
                        classes="sync-section",
                        markup=False,
                    )
                    for widget_id, (issue_id, field_name) in self._conflict_widgets.items():
                        pending = self._conflict_items[issue_id]
                        with Horizontal(classes="sync-conflict-field"):
                            yield Label(
                                Text(
                                    _one_line(
                                        f"{pending.key} · "
                                        f"{_(_FIELD_LABELS.get(field_name, field_name))}"
                                    )
                                ),
                                classes="sync-conflict-label",
                                markup=False,
                            )
                            yield Select(
                                options=(
                                    (_("Keep undecided"), ConflictResolution.HOLD.value),
                                    (
                                        Text(
                                            _("Use {provider} version").format(
                                                provider=self.provider_name
                                            )
                                        ),
                                        ConflictResolution.PROVIDER.value,
                                    ),
                                    (_("Send local version"), ConflictResolution.LOCAL.value),
                                ),
                                allow_blank=False,
                                compact=True,
                                id=widget_id,
                                classes="dropdown",
                            )
            if self.plan.conflicts():
                with Horizontal(id="sync-conflict-buttons"):
                    yield Button(
                        Text(
                            _("Use {provider} version").format(
                                provider=self.provider_name
                            )
                        ),
                        id="sync-use-provider",
                    )
                    yield Button(
                        Text(
                            _("Overwrite {provider}").format(
                                provider=self.provider_name
                            )
                        ),
                        variant="error",
                        id="sync-force",
                    )
            with Horizontal(id="sync-buttons"):
                yield Button(_("Cancel"), id="sync-cancel")
                yield Button(_("Pull only"), id="sync-pull")
                yield Button(_("Send ready changes"), variant="primary", id="sync-send")

    def on_mount(self) -> None:
        # The safe choice owns initial focus. Sending always takes a deliberate
        # click or Y rather than an accidental Enter.
        self.query_one("#sync-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = {
            "sync-send": SyncChoice.SEND,
            "sync-pull": SyncChoice.PULL,
            "sync-use-provider": SyncChoice.USE_PROVIDER,
            "sync-force": SyncChoice.FORCE,
            "sync-cancel": SyncChoice.CANCEL,
        }.get(str(event.button.id), SyncChoice.CANCEL)
        if choice is SyncChoice.USE_PROVIDER:
            self.dismiss(SyncDecision(choice, self._all_conflicts(ConflictResolution.PROVIDER)))
        elif choice is SyncChoice.FORCE:
            self.dismiss(SyncDecision(choice, self._all_conflicts(ConflictResolution.LOCAL)))
        elif choice is SyncChoice.SEND:
            self.dismiss(SyncDecision(choice, self._selected_conflicts()))
        else:
            self.dismiss(SyncDecision(choice))

    def action_send(self) -> None:
        self.dismiss(SyncDecision(SyncChoice.SEND, self._selected_conflicts()))

    def action_pull(self) -> None:
        self.dismiss(SyncDecision(SyncChoice.PULL))

    def action_cancel(self) -> None:
        self.dismiss(SyncDecision(SyncChoice.CANCEL))

    def _selected_conflicts(self) -> dict[str, dict[str, ConflictResolution]]:
        selected: dict[str, dict[str, ConflictResolution]] = {}
        for widget_id, (issue_id, field_name) in self._conflict_widgets.items():
            raw = self.query_one(f"#{widget_id}", Select).value
            resolution = ConflictResolution(str(raw))
            selected.setdefault(issue_id, {})[field_name] = resolution
        return selected

    def _all_conflicts(self, resolution: ConflictResolution) -> dict[str, dict[str, ConflictResolution]]:
        selected: dict[str, dict[str, ConflictResolution]] = {}
        for issue_id, field_name in self._conflict_widgets.values():
            selected.setdefault(issue_id, {})[field_name] = resolution
        return selected


class SyncProgressScreen(ModalScreen[None]):
    """Show truthful, provider-neutral sync work until it is acknowledged."""

    BINDINGS = [
        Binding("escape,enter", "close", "Close", show=False),
        Binding(
            "f5,n,r,m,c,T,slash,f2,ctrl+p",
            "block",
            "Sync active",
            priority=True,
            show=False,
        ),
    ]

    class Progressed(Message):
        """Marshal a synchronous worker callback onto Textual's event loop."""

        def __init__(self, update: SyncProgressUpdate) -> None:
            self.update = update
            super().__init__()

        def can_replace(self, message: Message) -> bool:
            return isinstance(message, SyncProgressScreen.Progressed)

    def __init__(self, provider: str, project: str) -> None:
        super().__init__()
        self.provider_name = _one_line(provider)
        self.project_name = _one_line(project)
        self._update = SyncProgressUpdate(
            phase=SyncPhase.PREPARING,
            summary=_('Checking local Markdown and provider state'),
        )
        self._terminal = False

    def compose(self) -> ComposeResult:
        with Vertical(id="sync-progress-dialog"):
            yield Label(
                Text(_('Syncing with {provider}').format(provider=self.provider_name)),
                id="sync-progress-heading",
                markup=False,
            )
            yield Label(Text(self.project_name), id="sync-progress-destination", markup=False)
            with VerticalScroll(id="sync-progress-content", can_focus=True):
                yield LoadingIndicator(id="sync-progress-spinner")
                yield Label("", id="sync-progress-phase", markup=False)
                yield Label("", id="sync-progress-fraction", markup=False)
                yield Label("", id="sync-progress-item", markup=False)
                yield Label("", id="sync-progress-summary", markup=False)
            with Horizontal(id="sync-progress-buttons"):
                yield Button(_("Close"), id="sync-progress-close", disabled=True)

    def on_mount(self) -> None:
        self._sync_compact_layout()
        self._apply_update()

    def on_resize(self, _event: events.Resize) -> None:
        """Keep the truthful phase and fraction visible in tiny terminals."""
        self._sync_compact_layout()

    def _sync_compact_layout(self) -> None:
        self.set_class(self.size.height < 16, "compact-sync-progress")

    def update_progress(self, update: SyncProgressUpdate) -> None:
        """Accept one progress update from either the UI or sync thread."""
        update = replace(
            update,
            item=_one_line(update.item),
            summary=_one_line(update.summary),
        )
        # Store before posting so a same-loop caller that immediately marks a
        # terminal result retains the newest truthful fraction. Widget writes
        # still happen only when Textual handles the thread-safe message.
        if not self._terminal:
            self._update = update
        self.post_message(self.Progressed(update))

    def on_sync_progress_screen_progressed(self, event: Progressed) -> None:
        if self._terminal:
            return
        self._update = event.update
        if not event.update.active:
            self._terminal = True
        self._apply_update()

    def finish_success(self, report: SyncReport) -> None:
        """Show a stable success or held-work result."""
        phase = SyncPhase.HELD if report.held or report.skipped else SyncPhase.COMPLETE
        self._finish(phase, report.summary(), error=False)

    def finish_error(self, message: str) -> None:
        """Keep a safe failure result visible instead of replacing it with a toast."""
        self._finish(SyncPhase.FAILED, message, error=True)

    def begin_board_refresh(self) -> None:
        """Keep the result locked while refreshed widgets catch up."""
        if self._terminal:
            return
        self._update = replace(
            self._update,
            phase=SyncPhase.FINALIZING,
            summary=_("Refreshing the local board"),
            active=True,
            error=False,
        )
        self._apply_update()

    def finish_refresh_error(self, report: SyncReport, message: str) -> None:
        """Name a widget refresh failure without implying provider writes failed."""
        phase = SyncPhase.HELD if report.held or report.skipped else SyncPhase.COMPLETE
        summary = _(
            "Provider sync completed · Board refresh failed: {error} · {report}"
        ).format(report=report.summary(), error=_one_line(message))
        self._finish(phase, summary, error=True)

    def finish_local_reload_error(self, report: SyncReport, message: str) -> None:
        """Name a backend local reload failure after provider writes succeeded."""
        phase = SyncPhase.HELD if report.held or report.skipped else SyncPhase.COMPLETE
        summary = _(
            "Provider sync completed · Local board reload failed: {error} · {report}"
        ).format(report=report.summary(), error=_one_line(message))
        self._finish(phase, summary, error=True)

    def _finish(self, phase: SyncPhase, summary: str, *, error: bool) -> None:
        current = self._update
        self._update = SyncProgressUpdate(
            phase=phase,
            completed=current.completed,
            total=current.total,
            item=current.item,
            summary=_one_line(summary),
            active=False,
            error=error,
        )
        self._terminal = True
        self._apply_update()

    def _ready(self) -> bool:
        """Whether the composed controls still belong to this screen."""
        return bool(self.query("#sync-progress-phase"))

    def _apply_update(self) -> None:
        """Render stored state only while Textual owns the composed controls."""
        if not self._ready():
            return
        self._render_update(self._update)
        if self._terminal:
            self._settle()

    def _settle(self) -> None:
        spinner = self.query_one("#sync-progress-spinner", LoadingIndicator)
        spinner.auto_refresh = None
        spinner.display = False
        close = self.query_one("#sync-progress-close", Button)
        close.disabled = False
        close.focus()

    def _render_update(self, update: SyncProgressUpdate) -> None:
        phase = self.query_one("#sync-progress-phase", Label)
        phase.update(Text(_phase_label(update.phase)))
        phase.set_classes(f"sync-progress-{update.phase.value}")
        fraction = "— / —" if update.total is None else f"{update.completed} / {update.total}"
        if update.total is None and update.completed:
            fraction = ngettext(
                "{count} card fetched",
                "{count} cards fetched",
                update.completed,
            ).format(count=update.completed)
        elif update.total == 0:
            fraction = ngettext("{count} card", "{count} cards", 0).format(count=0)
        self.query_one("#sync-progress-fraction", Label).update(Text(fraction))
        self.query_one("#sync-progress-item", Label).update(Text(update.item or "—"))
        self.query_one("#sync-progress-summary", Label).update(Text(update.summary or "—"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sync-progress-close":
            self.action_close()

    def action_close(self) -> None:
        if self._terminal:
            self.dismiss(None)

    def action_block(self) -> None:
        """An active provider write cannot be cancelled safely."""

    def on_unmount(self) -> None:
        spinners = self.query("#sync-progress-spinner")
        if spinners:
            spinners.first(LoadingIndicator).auto_refresh = None


def _phase_label(phase: SyncPhase) -> str:
    return {
        SyncPhase.PREPARING: _("Preparing"),
        SyncPhase.APPLYING: _("Applying changes"),
        SyncPhase.FETCHING: _("Fetching cards"),
        SyncPhase.COMMENTS: _("Fetching comments"),
        SyncPhase.RECONCILING: _("Reconciling Markdown"),
        SyncPhase.VERIFYING: _("Verifying removed cards"),
        SyncPhase.FINALIZING: _("Finalizing"),
        SyncPhase.COMPLETE: _("Complete"),
        SyncPhase.HELD: _("Complete · Held locally"),
        SyncPhase.FAILED: _("Failed"),
    }[phase]


def _one_line(value: str, limit: int = 160) -> str:
    terminal_safe = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character
        for character in str(value)
    )
    text = " ".join(terminal_safe.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _multiline(value: str) -> str:
    """Preserve plan line breaks while removing terminal control characters."""
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    terminal_safe = "".join(
        character
        if character == "\n" or ord(character) >= 32 and ord(character) != 127
        else " "
        for character in normalized
    )
    return "\n".join(" ".join(line.split()) for line in terminal_safe.split("\n"))


__all__ = ["SyncChoice", "SyncConfirmScreen", "SyncDecision", "SyncProgressScreen"]
