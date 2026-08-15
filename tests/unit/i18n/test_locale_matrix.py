"""Compatibility coverage for common operating-system locale families."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from babel.messages.pofile import read_po

from pykantui.i18n import Locale, resolve_locale, translate, using_locale

SUPPORTED_REGIONAL_LOCALES = {
    "ar_SA.UTF-8": Locale.ARABIC,
    "de_DE.UTF-8": Locale.GERMAN,
    "en_US.UTF-8": Locale.ENGLISH,
    "es_MX.UTF-8": Locale.SPANISH,
    "fr_CA.UTF-8": Locale.FRENCH,
    "hi_IN.UTF-8": Locale.HINDI,
    "id_ID.UTF-8": Locale.INDONESIAN,
    "it_IT.UTF-8": Locale.ITALIAN,
    "ja_JP.UTF-8": Locale.JAPANESE,
    "ko_KR.UTF-8": Locale.KOREAN,
    "nl_NL.UTF-8": Locale.DUTCH,
    "pl_PL.UTF-8": Locale.POLISH,
    "pt_BR.UTF-8": Locale.PORTUGUESE,
    "ru_RU.UTF-8": Locale.RUSSIAN,
    "th_TH.UTF-8": Locale.THAI,
    "tr_TR.UTF-8": Locale.TURKISH,
    "uk_UA.UTF-8": Locale.UKRAINIAN,
    "vi_VN.UTF-8": Locale.VIETNAMESE,
    "zh_CN.UTF-8": Locale.SIMPLIFIED_CHINESE,
    "zh-Hans-CN.UTF-8": Locale.SIMPLIFIED_CHINESE,
    "zh_TW.UTF-8": Locale.TRADITIONAL_CHINESE,
    "zh-Hant-HK.UTF-8": Locale.TRADITIONAL_CHINESE,
}

TRANSLATED_LOCALES = tuple(SUPPORTED_REGIONAL_LOCALES.values())
CATALOG_DIRECTORY = Path(__file__).parents[3] / "src" / "pykantui" / "i18n" / "locales"
FORMAT_TOKEN = re.compile(r"\{[^{}]+\}|\[[^\]]+\]|`[^`]*`")

# These scripts are safe input today, but do not have bundled translations.
# Keeping this list explicit prevents a missing catalog from masquerading as a
# translated interface when a machine happens to use one of these locales.
COMMON_FALLBACK_LOCALES = (
    "he_IL.UTF-8",
    "pt_PT.UTF-8",
)

COMMON_SCRIPT_SAMPLES = (
    "Überprüfung der Anmeldung",
    "Revisão do pagamento",
    "Aggiornare la documentazione",
    "Проверить синхронизацию",
    "Перевірити синхронізацію",
    "支払い同期を確認",
    "결제 동기화 확인",
    "भुगतान सिंक जाँचें",
    "مراجعة مزامنة الدفع",
    "בדיקת סנכרון תשלום",
    "付款同步检查",
)


def catalog_forms(value: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """Return singular and plural catalog values as one immutable shape."""

    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


class CommonLocaleResolutionTests(unittest.TestCase):
    def test_supported_regional_locales_resolve_to_their_catalog(self) -> None:
        for tag, expected in SUPPORTED_REGIONAL_LOCALES.items():
            with self.subTest(tag=tag):
                self.assertIs(resolve_locale(tag, environ={}, system_locale="C"), expected)

    def test_common_untranslated_locales_fall_back_to_english(self) -> None:
        for tag in COMMON_FALLBACK_LOCALES:
            with self.subTest(tag=tag):
                self.assertIs(resolve_locale(tag, environ={}, system_locale="C"), Locale.ENGLISH)

    def test_common_scripts_are_never_changed_as_if_they_were_ui_text(self) -> None:
        for locale in TRANSLATED_LOCALES:
            with self.subTest(locale=locale), using_locale(locale):
                self.assertEqual(COMMON_SCRIPT_SAMPLES, tuple(translate(value) for value in COMMON_SCRIPT_SAMPLES))

    def test_common_catalogs_translate_primary_terminal_vocabulary(self) -> None:
        expected = {
            Locale.ARABIC: ("إلغاء", "بحث", "حفظ"),
            Locale.DUTCH: ("Annuleren", "Zoeken", "Opslaan"),
            Locale.GERMAN: ("Abbrechen", "Suchen", "Speichern"),
            Locale.HINDI: ("रद्द करें", "खोजें", "सहेजें"),
            Locale.INDONESIAN: ("Batal", "Cari", "Simpan"),
            Locale.PORTUGUESE: ("Cancelar", "Pesquisar", "Salvar"),
            Locale.ITALIAN: ("Annulla", "Cerca", "Salva"),
            Locale.POLISH: ("Anuluj", "Szukaj", "Zapisz"),
            Locale.SIMPLIFIED_CHINESE: ("取消", "搜索", "保存"),
            Locale.THAI: ("ยกเลิก", "ค้นหา", "บันทึก"),
            Locale.TRADITIONAL_CHINESE: ("取消", "搜尋", "儲存"),
            Locale.TURKISH: ("İptal", "Ara", "Kaydet"),
            Locale.UKRAINIAN: ("Скасувати", "Пошук", "Зберегти"),
            Locale.VIETNAMESE: ("Hủy", "Tìm kiếm", "Lưu"),
            Locale.JAPANESE: ("キャンセル", "検索", "保存"),
            Locale.KOREAN: ("취소", "검색", "저장"),
            Locale.RUSSIAN: ("Отмена", "Поиск", "Сохранить"),
        }

        for locale, translations in expected.items():
            with self.subTest(locale=locale), using_locale(locale):
                self.assertEqual(translations, tuple(translate(value) for value in ("Cancel", "Search", "Save")))

    def test_simplified_chinese_uses_board_domain_vocabulary(self) -> None:
        source = ("Board", "Summary", "Assignee", "Reporter", "Key", "Sprint", "age {days}d")
        expected = ("看板", "摘要", "负责人", "报告人", "编号", "迭代", "{days}天前")

        with using_locale(Locale.SIMPLIFIED_CHINESE):
            self.assertEqual(expected, tuple(translate(value) for value in source))

    def test_traditional_chinese_uses_board_domain_vocabulary(self) -> None:
        source = ("Board", "Summary", "Assignee", "Reporter", "Key", "Sprint", "age {days}d")
        expected = ("看板", "摘要", "負責人", "報告人", "編號", "迭代", "{days}天前")

        with using_locale(Locale.TRADITIONAL_CHINESE):
            self.assertEqual(expected, tuple(translate(value) for value in source))

    def test_every_translated_catalog_is_complete_and_preserves_format_tokens(self) -> None:
        with (CATALOG_DIRECTORY / "pykantui.pot").open(encoding="utf-8") as stream:
            template = read_po(stream)
        source_messages = [message for message in template if message.id]

        for locale in TRANSLATED_LOCALES:
            if locale is Locale.ENGLISH:
                continue
            path = CATALOG_DIRECTORY / locale.value / "LC_MESSAGES" / "pykantui.po"
            contents = path.read_text(encoding="utf-8")
            with self.subTest(locale=locale), path.open(encoding="utf-8") as stream:
                for placeholder in (
                    "ORGANIZATION",
                    "FULL NAME",
                    "EMAIL@ADDRESS",
                    "PROJECT VERSION",
                    "YEAR-MO-DA",
                ):
                    self.assertNotIn(placeholder, contents)
                catalog = read_po(stream)
                translated = [message for message in catalog if message.id]
                translated_by_id = {message.id: message for message in translated}
                self.assertEqual(
                    {message.id for message in source_messages},
                    set(translated_by_id),
                )
                for source in source_messages:
                    message = translated_by_id[source.id]
                    self.assertNotIn("fuzzy", message.flags)
                    strings = catalog_forms(message.string)
                    self.assertTrue(strings)
                    self.assertTrue(all(strings))
                    source_ids = catalog_forms(source.id)
                    expected_tokens = [sorted(FORMAT_TOKEN.findall(value)) for value in source_ids]
                    for index, value in enumerate(strings):
                        source_index = min(index, len(expected_tokens) - 1)
                        self.assertEqual(expected_tokens[source_index], sorted(FORMAT_TOKEN.findall(value)))


if __name__ == "__main__":
    unittest.main()
