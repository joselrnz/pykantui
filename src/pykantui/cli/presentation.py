"""Small, capability-aware presentation helpers for interactive commands."""

from __future__ import annotations

CYAN = "\x1b[38;2;0;200;255m"
BLUE = "\x1b[38;2;111;178;255m"
RESET = "\x1b[0m"

_COMPACT_LOGO = r""" ____  __   __ _  __    _    _   _ _____ _   _ ___
|  _ \ \ \ / /| |/ /   / \  | \ | |_   _| | | |_ _|
| |_) | \ V / | ' /   / _ \ |  \| | | | | | | || |
|  __/   | |  | . \  / ___ \| |\  | | | | |_| || |
|_|      |_|  |_|\_\/_/   \_\_| \_| |_|  \___/|___|"""

_WIDE_LOGO = r"""     ___           ___           ___           ___           ___
    /\  \         |\__\         /\__\         /\  \         /\__\
   /::\  \        |:|  |       /:/  /        /::\  \       /::|  |
  /:/\:\  \       |:|  |      /:/__/        /:/\:\  \     /:|:|  |
 /::\~\:\  \      |:|__|__   /::\__\____   /::\~\:\  \   /:/|:|  |__
/:/\:\ \:\__\     /::::\__\ /:/\:::::\__\ /:/\:\ \:\__\ /:/ |:| /\__\
\/__\:\/:/  /    /:/~~/~    \/_|:|~~|~    \/__\:\/:/  / \/__|:|/:/  /
     \::/  /    /:/  /         |:|  |          \::/  /      |:/:/  /
      \/__/     \/__/          |:|  |          /:/  /       |::/  /
                               |:|  |         /:/  /        /:/  /
                                \|__|         \/__/         \/__/
                       ___           ___
                      /\  \         /\__\          ___
                      \:\  \       /:/  /         /\  \
                       \:\  \     /:/  /          \:\  \
                       /::\  \   /:/  /  ___      /::\__\
                      /:/\:\__\ /:/__/  /\__\  \/:/\/__/
                     /:/  \/__/ \:\  \ /:/  / /\/:/  /
                    /:/  /       \:\  /:/  /  \::/__/
                    \/__/         \:\/:/  /    \:\__\
                                   \::/  /      \/__/
                                    \/__/"""

_TAGLINE = "PYKANTUI  //  LOCAL-FIRST BOARDS  //  PROVIDER SYNC"

_LOADER_BLOCK_LOGO = """██████╗ ██╗   ██╗██╗  ██╗ █████╗ ███╗   ██╗████████╗██╗   ██╗██╗
██╔══██╗╚██╗ ██╔╝██║ ██╔╝██╔══██╗████╗  ██║╚══██╔══╝██║   ██║██║
██████╔╝ ╚████╔╝ █████╔╝ ███████║██╔██╗ ██║   ██║   ██║   ██║██║
██╔═══╝   ╚██╔╝  ██╔═██╗ ██╔══██║██║╚██╗██║   ██║   ██║   ██║██║
██║        ██║   ██║  ██╗██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║
╚═╝        ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝"""


def render_intro(*, color: bool, width: int = 100, compact: bool = False) -> str:
    """Return the static ASCII intro, optionally in Cyberpunk truecolor."""
    logo = _WIDE_LOGO if width >= 80 and not compact else _COMPACT_LOGO
    if not color:
        return f"{logo}\n{_TAGLINE}"
    return f"{CYAN}{logo}{RESET}\n{BLUE}{_TAGLINE}{RESET}"


def render_loader_intro(*, width: int = 100) -> str:
    """Return the medium-width Unicode wordmark used by the Textual loader."""
    logo = _COMPACT_LOGO if width < 76 else _LOADER_BLOCK_LOGO
    logo = _extrude_logo(logo)
    return f"{logo}\n{_TAGLINE}"


def _extrude_logo(logo: str) -> str:
    """Add two single-cell shade layers behind a wordmark for 3D depth."""
    lines = logo.splitlines()
    width = max(map(len, lines))
    canvas = [[" "] * (width + 4) for _ in range(len(lines) + 2)]

    for shade, offset_x, offset_y in (("░", 4, 2), ("▒", 2, 1)):
        for row, line in enumerate(lines):
            for column, character in enumerate(line):
                if not character.isspace():
                    canvas[row + offset_y][column + offset_x] = shade

    for row, line in enumerate(lines):
        for column, character in enumerate(line):
            if not character.isspace():
                canvas[row][column] = character

    return "\n".join("".join(row).rstrip() for row in canvas)
