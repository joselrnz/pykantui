"""Saved locale preferences remain typed and tolerant of hand edits."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pykantui.config import BoardConfig
from pykantui.i18n import Locale


class LocaleConfigTests(unittest.TestCase):
    def test_default_locale_is_auto(self) -> None:
        self.assertIs(BoardConfig().locale, Locale.AUTO)

    def test_locale_round_trips_through_config_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = BoardConfig(locale=Locale.SPANISH)
            config.save(path)

            loaded = BoardConfig.load(path)

        self.assertIs(loaded.locale, Locale.SPANISH)

    def test_french_locale_round_trips_through_config_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = BoardConfig(locale=Locale.FRENCH)
            config.save(path)

            loaded = BoardConfig.load(path)

        self.assertIs(loaded.locale, Locale.FRENCH)

    def test_common_locales_round_trip_through_config_json(self) -> None:
        common = (
            Locale.ARABIC,
            Locale.DUTCH,
            Locale.GERMAN,
            Locale.HINDI,
            Locale.INDONESIAN,
            Locale.PORTUGUESE,
            Locale.ITALIAN,
            Locale.POLISH,
            Locale.SIMPLIFIED_CHINESE,
            Locale.THAI,
            Locale.TRADITIONAL_CHINESE,
            Locale.TURKISH,
            Locale.UKRAINIAN,
            Locale.VIETNAMESE,
            Locale.JAPANESE,
            Locale.KOREAN,
            Locale.RUSSIAN,
        )
        for locale in common:
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                BoardConfig(locale=locale).save(path)
                self.assertIs(BoardConfig.load(path).locale, locale)

    def test_unknown_hand_edited_locale_falls_back_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"locale": "not-a-locale"}), encoding="utf-8")

            loaded = BoardConfig.load(path)

        self.assertIs(loaded.locale, Locale.AUTO)


if __name__ == "__main__":
    unittest.main()
