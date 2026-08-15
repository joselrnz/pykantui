"""Locale selection without process-wide gettext mutation."""

from __future__ import annotations

import locale as system_locale_module
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum


class Locale(StrEnum):
    """Interface languages understood by pykantui."""

    AUTO = "auto"
    ARABIC = "ar"
    DUTCH = "nl"
    ENGLISH = "en"
    FRENCH = "fr"
    GERMAN = "de"
    HINDI = "hi"
    INDONESIAN = "id"
    ITALIAN = "it"
    JAPANESE = "ja"
    KOREAN = "ko"
    POLISH = "pl"
    PORTUGUESE = "pt"
    RUSSIAN = "ru"
    SIMPLIFIED_CHINESE = "zh"
    SPANISH = "es"
    THAI = "th"
    TRADITIONAL_CHINESE = "zh_TW"
    TURKISH = "tr"
    UKRAINIAN = "uk"
    VIETNAMESE = "vi"


_CURRENT_LOCALE: ContextVar[Locale] = ContextVar("pykantui_locale", default=Locale.ENGLISH)


def _language(value: object) -> Locale | None:
    """Reduce a locale tag such as ``es_MX.UTF-8`` to a supported language."""
    if isinstance(value, Locale):
        return None if value is Locale.AUTO else value
    if not isinstance(value, str):
        return None
    normalized = (
        value.split(":", 1)[0]
        .strip()
        .lower()
        .replace("-", "_")
        .split(".", 1)[0]
        .split("@", 1)[0]
    )
    language = normalized.split("_", 1)[0]
    if language == "zh":
        if normalized == "zh" or normalized.startswith(("zh_cn", "zh_sg", "zh_hans")):
            return Locale.SIMPLIFIED_CHINESE
        if normalized.startswith(("zh_tw", "zh_hk", "zh_mo", "zh_hant")):
            return Locale.TRADITIONAL_CHINESE
        return None
    # The bundled Portuguese catalog follows Brazilian vocabulary. A bare
    # explicit ``pt`` selects it, while an operating-system ``pt_PT`` locale
    # falls back instead of receiving the wrong regional translation.
    if language == "pt" and not (normalized == "pt" or normalized.startswith("pt_br")):
        return None
    return {
        Locale.ARABIC.value: Locale.ARABIC,
        Locale.DUTCH.value: Locale.DUTCH,
        Locale.ENGLISH.value: Locale.ENGLISH,
        Locale.FRENCH.value: Locale.FRENCH,
        Locale.GERMAN.value: Locale.GERMAN,
        Locale.HINDI.value: Locale.HINDI,
        Locale.INDONESIAN.value: Locale.INDONESIAN,
        Locale.ITALIAN.value: Locale.ITALIAN,
        Locale.JAPANESE.value: Locale.JAPANESE,
        Locale.KOREAN.value: Locale.KOREAN,
        Locale.POLISH.value: Locale.POLISH,
        Locale.PORTUGUESE.value: Locale.PORTUGUESE,
        Locale.RUSSIAN.value: Locale.RUSSIAN,
        Locale.SPANISH.value: Locale.SPANISH,
        Locale.THAI.value: Locale.THAI,
        Locale.TURKISH.value: Locale.TURKISH,
        Locale.UKRAINIAN.value: Locale.UKRAINIAN,
        Locale.VIETNAMESE.value: Locale.VIETNAMESE,
    }.get(language)


def resolve_locale(
    requested: Locale | str | None = None,
    *,
    configured: Locale | str = Locale.AUTO,
    environ: Mapping[str, str] | None = None,
    system_locale: str | None = None,
) -> Locale:
    """Resolve CLI, environment, saved, and operating-system preferences.

    An explicit command-line choice wins, followed by ``PYKANTUI_LOCALE``, the
    saved configuration, and the operating-system locale. Unknown values are
    ignored safely and English is the final fallback.
    """
    environment = os.environ if environ is None else environ
    detected = system_locale
    if detected is None:
        detected = system_locale_module.getlocale()[0]
    posix_locale = next(
        (
            environment[name]
            for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG")
            if environment.get(name)
        ),
        None,
    )
    for candidate in (requested, environment.get("PYKANTUI_LOCALE"), configured, posix_locale, detected):
        language = _language(candidate)
        if language is not None:
            return language
    return Locale.ENGLISH


def current_locale() -> Locale:
    """Return the interface locale active in this execution context."""
    return _CURRENT_LOCALE.get()


@contextmanager
def using_locale(locale: Locale | str) -> Iterator[Locale]:
    """Activate ``locale`` for one synchronous or asynchronous context."""
    resolved = _language(locale) or Locale.ENGLISH
    token = _CURRENT_LOCALE.set(resolved)
    try:
        yield resolved
    finally:
        _CURRENT_LOCALE.reset(token)
