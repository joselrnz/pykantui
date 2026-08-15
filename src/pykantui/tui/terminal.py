"""Terminal capability helpers for the Textual application."""

from __future__ import annotations

import os
import sys
from typing import Any, cast

from textual.app import App
from textual.events import Resize
from textual.geometry import Size


def current_terminal_size() -> Size | None:
    """Return the controlling terminal size when one is available.

    Docker Desktop can occasionally update the pseudo-terminal dimensions
    without forwarding ``SIGWINCH`` to the process.  Reading the dimensions
    directly lets the app recover on the next polling interval.  Tests and
    redirected output have no controlling terminal, so they return ``None``.
    """
    # Textual's Linux driver reads from stdin, so that controlling descriptor
    # is authoritative. Output streams remain useful fallbacks for uncommon
    # launchers that redirect only one side of the process.
    for stream in (sys.__stdin__, sys.__stdout__, sys.__stderr__):
        if stream is None:
            continue
        try:
            terminal_size = os.get_terminal_size(stream.fileno())
        except (AttributeError, OSError, ValueError):
            continue
        return Size(terminal_size.columns, terminal_size.lines)
    return None


class TerminalResizeMixin:
    """Keep a Textual app synchronized with its controlling terminal.

    Textual already handles ordinary resize events.  This small fallback is
    shared by standalone setup apps and the board for container environments
    that update the pseudo-terminal size without forwarding ``SIGWINCH``.
    """

    _TERMINAL_POLL_SECONDS = 0.5

    @property
    def _resize_app(self) -> App[Any]:
        """Return this mixin's Textual host with a precise static type."""
        return cast(App[Any], self)

    def _start_terminal_resize_monitor(self) -> None:
        """Start the low-cost missed-event recovery timer."""
        self._resize_app.set_interval(self._TERMINAL_POLL_SECONDS, self._poll_terminal_size)

    def _poll_terminal_size(self) -> None:
        """Post Textual's normal resize message when the host size changed."""
        app = self._resize_app
        size = current_terminal_size()
        if size is not None and size != app.size:
            app.post_message(Resize(size, size))
