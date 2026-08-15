"""Safe, cached links from local cards to their provider pages."""

from __future__ import annotations

import os
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.events import Click, MouseDown, MouseUp
from textual.widgets import Static

from pykantui.i18n import translate as _
from pykantui.models import Task

ISSUE_LINK_GLYPH = "↗"
"""A width-safe U+2197 action glyph for an external provider page."""


def safe_https_url(value: object) -> str:
    """Return a browser-safe HTTPS URL, or an empty string.

    Card Markdown is user-editable, so its cached ``url`` is untrusted input.
    Refusing credentials, control characters, backslashes and malformed ports
    avoids browser-parser ambiguities without maintaining a provider host
    allowlist that would break self-hosted installations.
    """
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or "\\" in candidate:
        return ""
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    if port is not None and not 1 <= port <= 65535:
        return ""
    return candidate


def provider_issue_url(task: Task) -> str:
    """Read one already-cached issue URL without touching the provider API."""
    return safe_https_url(task.metadata.get("url"))


def _running_in_container() -> bool:
    """Return whether a terminal process is isolated from the host desktop."""
    return bool(
        os.environ.get("container")  # noqa: SIM112 - Podman defines this lowercase name
        or os.environ.get("KUBERNETES_SERVICE_HOST")
        or Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
    )


def _copy_host_link(app: object, url: str) -> bool:
    """Copy a URL through Textual's OSC-52 host-terminal clipboard."""
    copier = getattr(app, "copy_to_clipboard", None)
    if not callable(copier):
        return False
    try:
        copier(url)
    except Exception:  # noqa: BLE001 - clipboard support belongs to the terminal
        return False

    notifier = getattr(app, "notify", None)
    if callable(notifier):
        notifier(
            _("Browser unavailable here · link copied · Ctrl+click ↗ or paste it"),
            severity="warning",
            timeout=8,
        )
    return True


def launch_external_url(
    app: object,
    value: object,
    *,
    browser_open: Callable[[str], object] | None = None,
    in_container: bool | None = None,
) -> bool:
    """Open one HTTPS URL using the runtime that owns the user's browser.

    Textual Web owns a real browser and therefore uses ``App.open_url``. A
    native terminal can observe the boolean returned by :mod:`webbrowser`
    directly. A Docker process has no access to the host desktop, so it skips
    the doomed in-container launch and sends the link to the host terminal's
    clipboard via Textual's OSC-52 support instead.

    ``browser_open`` and ``in_container`` are explicit seams for deterministic
    tests; normal callers should leave them unset.
    """
    url = safe_https_url(value)
    if not url:
        return False

    if getattr(app, "is_web", False) is True:
        opener = getattr(app, "open_url", None)
        if not callable(opener):
            return False
        try:
            return opener(url) is not False
        except Exception:  # noqa: BLE001 - the Textual web driver is platform code
            return False

    container_runtime = _running_in_container() if in_container is None else in_container
    if container_runtime:
        return _copy_host_link(app, url)

    opener = browser_open or webbrowser.open
    try:
        if bool(opener(url)):
            return True
    except Exception:  # noqa: BLE001 - registered browser programs are external
        pass
    return _copy_host_link(app, url)


def open_provider_url(app: object, value: object) -> bool:
    """Ask a Textual app to open one validated URL and report failure safely."""
    url = safe_https_url(value)
    if not url:
        return False
    notifier = getattr(app, "notify", None)
    try:
        opened = launch_external_url(app, url)
    except Exception:  # noqa: BLE001 - terminal/browser drivers are platform code
        if callable(notifier):
            notifier(_("Could not open the provider link"), severity="error", timeout=4)
        return False
    if not opened:
        if callable(notifier):
            notifier(_("Could not open the provider link"), severity="error", timeout=4)
        return False
    return True


class ProviderIssueLink(Static, can_focus=True):
    """One-cell mouse and keyboard action for a validated provider URL."""

    ALLOW_SELECT = False
    BINDINGS = [Binding("enter,space", "open_provider", "↗", show=False)]

    def __init__(
        self,
        url: object = "",
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        inherited = "provider-issue-link"
        self._provider_url = safe_https_url(url)
        super().__init__(
            self._link_text(),
            id=id,
            classes=f"{inherited} {classes}" if classes else inherited,
            markup=False,
        )
        self._set_availability()

    @property
    def provider_url(self) -> str:
        """The validated URL currently attached to this action."""
        return self._provider_url

    def set_provider_url(self, value: object) -> None:
        """Replace the cached target and hide the action when it is unsafe."""
        self._provider_url = safe_https_url(value)
        if self.is_running:
            self.update(self._link_text())
        self._set_availability()

    def _link_text(self) -> Text:
        """Render the glyph as an OSC-8 hyperlink when a safe URL exists."""
        return Text(
            ISSUE_LINK_GLYPH,
            style=Style(link=self._provider_url) if self._provider_url else Style.null(),
        )

    def _set_availability(self) -> None:
        """Keep display, focus and help synchronized with the current URL."""
        available = bool(self._provider_url)
        self.display = available
        self.disabled = not available
        if available and _running_in_container():
            self.tooltip = _("Ctrl+click to open · click to copy")
        else:
            self.tooltip = _("Open provider issue in browser") if available else None

    def on_mount(self) -> None:
        """Apply a URL changed between construction and mounting."""
        self.update(self._link_text())

    def action_open_provider(self) -> None:
        """Open the already-validated page through Textual's terminal driver."""
        open_provider_url(self.app, self._provider_url)

    def on_mouse_down(self, event: MouseDown) -> None:
        """Keep clicking the arrow from beginning a Kanban drag."""
        event.stop()

    def on_mouse_up(self, event: MouseUp) -> None:
        """Keep the matching release inside the link action."""
        event.stop()

    def on_click(self, event: Click) -> None:
        """Open once, without bubbling into card double-click handling."""
        event.stop()
        self.action_open_provider()


__all__ = [
    "ISSUE_LINK_GLYPH",
    "ProviderIssueLink",
    "launch_external_url",
    "open_provider_url",
    "provider_issue_url",
    "safe_https_url",
]
