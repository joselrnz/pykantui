"""Replace a completed command with a workspace board process."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pykantui.i18n import translate as _
from pykantui.tracker.errors import ProviderError


def replace_with_workspace_board(workspace: Path) -> None:
    """Open ``workspace`` without nesting a second TUI process.

    The caller must validate the workspace first. ``execv`` replaces the
    current command, so closing the selected board returns to the original
    shell instead of revealing another pykantui screen underneath it.
    """

    try:
        os.chdir(workspace)
        _reattach_controlling_terminal()
        os.execv(sys.executable, [sys.executable, "-m", "pykantui"])
    except OSError as error:
        raise ProviderError(
            _("the selected workspace could not be opened"),
            hint=_("Run `kbn` from this directory: {workspace}").format(workspace=workspace),
        ) from error


def _reattach_controlling_terminal() -> None:
    """Restore standard streams after a standalone Textual chooser on POSIX."""

    if os.name != "posix":
        return
    try:
        terminal = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return
    try:
        for standard_stream in (0, 1, 2):
            os.dup2(terminal, standard_stream)
    finally:
        if terminal > 2:
            os.close(terminal)


__all__ = ["replace_with_workspace_board"]
