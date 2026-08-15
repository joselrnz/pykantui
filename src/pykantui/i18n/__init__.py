"""Explicit, context-safe internationalization for the CLI and TUI."""

from pykantui.i18n.locale import Locale, current_locale, resolve_locale, using_locale
from pykantui.i18n.translator import ntranslate, translate

__all__ = [
    "Locale",
    "current_locale",
    "ntranslate",
    "resolve_locale",
    "translate",
    "using_locale",
]
