"""Gettext catalog loading and explicit translation functions."""

from __future__ import annotations

import gettext
from functools import lru_cache
from pathlib import Path

from pykantui.i18n.locale import Locale, current_locale

DOMAIN = "pykantui"
LOCALE_DIRECTORY = Path(__file__).with_name("locales")


@lru_cache(maxsize=len(Locale))
def _catalog(locale: Locale) -> gettext.NullTranslations:
    """Load one immutable catalog, falling back to source strings."""
    if locale is Locale.ENGLISH:
        return gettext.NullTranslations()
    return gettext.translation(
        DOMAIN,
        localedir=LOCALE_DIRECTORY,
        languages=[locale.value],
        fallback=True,
    )


def translate(message: str) -> str:
    """Translate an application-owned message in the active context."""
    return _catalog(current_locale()).gettext(message)


def ntranslate(singular: str, plural: str, count: int) -> str:
    """Translate a plural application message in the active context."""
    return _catalog(current_locale()).ngettext(singular, plural, count)
