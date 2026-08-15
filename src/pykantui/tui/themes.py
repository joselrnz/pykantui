"""Themes this app ships in addition to Textual's built-ins."""

from __future__ import annotations

from textual.theme import Theme

#: Vercel's palette: true black, white text, one electric blue, and greys in
#: between. Nothing is tinted — the neutrals are pure greyscale, which is what
#: makes the blue read as loudly as it does.
VERCEL = Theme(
    name="vercel",
    dark=True,
    background="#000000",
    surface="#0A0A0A",
    panel="#111111",
    foreground="#EDEDED",
    primary="#0070F3",
    secondary="#7928CA",
    accent="#0070F3",
    success="#50E3C2",
    warning="#F5A623",
    error="#FF0080",
    boost="#1A1A1A",
    variables={
        "border": "#333333",
        "border-blurred": "#222222",
        "text-muted": "#888888",
        "text-disabled": "#444444",
        "block-cursor-background": "#0070F3",
        "block-cursor-foreground": "#000000",
        "input-selection-background": "#0070F3 35%",
        "scrollbar": "#222222",
        "scrollbar-hover": "#333333",
        "scrollbar-active": "#0070F3",
        "footer-key-foreground": "#0070F3",
    },
)

#: Neon cyan and electric blue over a GitHub-dark-style neutral foundation.
#: The base and component tokens come from the user's cyberpunk reference;
#: explicit variables keep focus, selection, footer, and scrollbar states
#: stable instead of relying on Textual's generated lighten/darken values.
CYBERPUNK = Theme(
    name="cyberpunk",
    dark=True,
    background="#0A0E14",
    surface="#0D1117",
    panel="#11161D",
    foreground="#C9D1D9",
    primary="#0078DC",
    secondary="#00A0FF",
    accent="#00C8FF",
    success="#98C379",
    warning="#E5C07B",
    error="#E06C75",
    boost="#1D2026",
    variables={
        "border": "#6FB2FF",
        "border-blurred": "#003C6E",
        "text-primary": "#56A5E7",
        "text-secondary": "#56C0FF",
        "text-accent": "#56DAFF",
        "text-success": "#BBD7A6",
        "text-warning": "#EDD5A7",
        "text-error": "#EA9DA3",
        "block-cursor-background": "#0078DC",
        "block-cursor-foreground": "#C9D1D9",
        "input-selection-background": "#378AF1 40%",
        "footer-background": "#11161D",
        "footer-key-foreground": "#00C8FF",
        "footer-foreground": "#C9D1D9",
        "scrollbar": "#003058",
        "scrollbar-hover": "#003C6E",
        "scrollbar-active": "#0078DC",
    },
)

# The reference needs no entry here: jiratui defines no palette of its own
# (`DEFAULT_THEME = 'textual-dark'`, reference/jiratui/src/jiratui/app.py:52),
# so `kbn --theme textual-dark` already is the reference look. What makes
# jiratui distinctive is its stylesheet — transparent backgrounds and
# `round $primary-lighten-3` field borders — which we copied.

#: The default. textual-dark with two values changed, and only two.
#:
#: **panel** was ``#242F38`` -- a blue-grey, not a grey. Textual draws the
#: Header, the Footer and every dropdown in ``$panel``, so one navy value put a
#: blue cast on the top bar, the bottom bar and eight rules in ``app.tcss`` at
#: once. ``#1F1F1F`` is the same weight without the hue.
#:
#: **accent** was ``#FEA62B`` -- byte-identical to ``warning``. So "this is
#: selected" and "this needs attention" were the same colour, which is why the
#: NEW sync state had to be moved onto ``$primary`` to stay distinguishable
#: from EDITED. Teal is clear of both the blue borders and the amber warnings,
#: which gives the four card states their own colours back.
#:
#: Everything else is textual-dark's, deliberately: primary stays ``#0178D4``
#: because the blue frame is the look, and background and surface are already
#: neutral.
PYKANTUI_DARK = Theme(
    name="pykantui-dark",
    dark=True,
    background="#121212",
    surface="#1E1E1E",
    panel="#1F1F1F",
    foreground="#E0E0E0",
    primary="#0178D4",
    secondary="#004578",
    accent="#2DD4BF",
    success="#4EBF71",
    warning="#FEA62B",
    error="#BA3C5B",
)

#: Every theme defined here, registered on startup.
CUSTOM_THEMES = [PYKANTUI_DARK, CYBERPUNK, VERCEL]
