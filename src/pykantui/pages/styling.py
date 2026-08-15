"""One place that knows how a standalone dialog should look.

``kbn init`` opens its pickers as their own Textual apps, because the wizard is
a plain terminal program with no app running. That made them the only screens
in pykantui that never registered the custom themes or read the configured one,
so they came up in stock ``textual-dark`` while ``kbn`` itself came up in
whatever the user had chosen. Two different-looking dialogs in one command.

Anything that opens a standalone dialog mixes this in.
"""

from __future__ import annotations

from typing import Any

from pykantui.config import DEFAULT_THEME, BoardConfig
from pykantui.tui.themes import CUSTOM_THEMES


class Themed:
    """Applies the user's theme to a standalone app.

    Mixed into an ``App``; ``apply_theme`` is called from ``on_mount`` before
    anything is drawn.
    """

    def apply_theme(self: Any) -> None:
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)

        try:
            wanted = BoardConfig.load().theme or DEFAULT_THEME
        except (OSError, ValueError):
            # A broken config should not stop a folder being chosen.
            wanted = DEFAULT_THEME

        self.theme = wanted if wanted in self.available_themes else DEFAULT_THEME


#: Shared chrome for the two pickers, in jiratui's idiom.
#:
#: jiratui defines no palette of its own -- it runs on stock ``textual-dark``,
#: the same colours we have. What makes it read calmly is *which strength* of
#: each it reaches for: a grey ``$foreground`` frame, pale
#: ``$primary-lighten-3`` on fields, translucent ``$background`` behind the
#: tree, and full-saturation colour kept for focus and state.
#:
#: We had it the other way round -- ``$primary`` at full strength on the frame
#: (#0178D4) over an opaque ``$panel`` slab -- so the chrome shouted and the
#: parts that carry meaning had nothing left to say.
#:
#: Written against variables throughout, so a theme switch restyles all of it.
DIALOG_CSS = """
.pk-dialog {
    width: 78;
    height: 30;
    border: round $primary;
    background: $panel;
    padding: 1 2;
}
/* The question in plain white, the answer in accent. Colouring both made the
   title compete with the path for the one thing that actually changes. */
.pk-title { text-style: bold; }
.pk-path  { color: $accent; text-style: bold; }
.pk-help  { color: $text-muted; }
.pk-error { color: $error; height: auto; }

/* Translucent, so the tree recedes and its contents come forward. */
.pk-panel {
    height: 1fr;
    border: round $border-blurred;
    margin: 1 0;
    background: $background 60%;
    scrollbar-size-vertical: 1;
}
.pk-buttons { height: auto; align-horizontal: right; }
.pk-buttons Button,
.pk-buttons Button.-primary {
    margin-left: 1;
    border: round $border-blurred;
    background: transparent;
    color: $text-muted;
    text-style: none;
}
.pk-buttons Button:hover,
.pk-buttons Button:focus,
.pk-buttons Button.-primary:hover,
.pk-buttons Button.-primary:focus {
    border: round $accent;
    background: transparent;
    color: $accent;
    text-style: bold;
}

/* Top and bottom bars, as on the board. The footer lists the screen's own
   bindings, so the keys shown cannot drift from the keys that work. */
Header { dock: top; }
Footer { dock: bottom; }
"""
