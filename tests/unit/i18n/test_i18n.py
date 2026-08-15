"""Locale resolution and gettext catalog behavior."""

from __future__ import annotations

import unittest

from pykantui.i18n import Locale, current_locale, ntranslate, resolve_locale, translate, using_locale


class LocaleResolutionTests(unittest.TestCase):
    def test_explicit_locale_wins_over_environment_and_saved_config(self) -> None:
        resolved = resolve_locale(
            Locale.SPANISH,
            configured=Locale.ENGLISH,
            environ={"PYKANTUI_LOCALE": "en"},
            system_locale="en_US.UTF-8",
        )

        self.assertIs(resolved, Locale.SPANISH)

    def test_environment_wins_over_saved_config(self) -> None:
        resolved = resolve_locale(
            configured=Locale.ENGLISH,
            environ={"PYKANTUI_LOCALE": "es_MX.UTF-8"},
            system_locale="en_US.UTF-8",
        )

        self.assertIs(resolved, Locale.SPANISH)

    def test_auto_uses_the_system_language_without_the_region(self) -> None:
        resolved = resolve_locale(
            Locale.AUTO,
            configured=Locale.AUTO,
            environ={},
            system_locale="es-MX",
        )

        self.assertIs(resolved, Locale.SPANISH)

    def test_auto_honors_standard_posix_locale_environment(self) -> None:
        resolved = resolve_locale(
            Locale.AUTO,
            configured=Locale.AUTO,
            environ={"LANG": "es_ES.UTF-8"},
            system_locale="en_US.UTF-8",
        )

        self.assertIs(resolved, Locale.SPANISH)

    def test_auto_recognizes_a_regional_french_locale(self) -> None:
        resolved = resolve_locale(
            Locale.AUTO,
            configured=Locale.AUTO,
            environ={"LANG": "fr_CA.UTF-8"},
            system_locale="en_US.UTF-8",
        )

        self.assertIs(resolved, Locale.FRENCH)

    def test_unknown_languages_fall_back_to_english(self) -> None:
        resolved = resolve_locale(
            Locale.AUTO,
            configured=Locale.AUTO,
            environ={"PYKANTUI_LOCALE": "xx_YY"},
            system_locale="C",
        )

        self.assertIs(resolved, Locale.ENGLISH)


class TranslationTests(unittest.TestCase):
    def test_spanish_catalog_translates_an_application_string(self) -> None:
        with using_locale(Locale.SPANISH):
            self.assertEqual("Cancelar", translate("Cancel"))

    def test_unknown_messages_preserve_provider_content(self) -> None:
        provider_title = "Fix OAuth callback"

        for locale in (Locale.SPANISH, Locale.FRENCH):
            with self.subTest(locale=locale), using_locale(locale):
                self.assertEqual(provider_title, translate(provider_title))

    def test_french_catalog_translates_an_application_string(self) -> None:
        with using_locale(Locale.FRENCH):
            self.assertEqual("Annuler", translate("Cancel"))

    def test_french_catalog_translates_singular_and_plural_cards(self) -> None:
        with using_locale(Locale.FRENCH):
            self.assertEqual("carte", ntranslate("card", "cards", 1))
            self.assertEqual("cartes", ntranslate("card", "cards", 2))

    def test_locale_context_is_restored(self) -> None:
        before = current_locale()

        with using_locale(Locale.SPANISH):
            self.assertIs(current_locale(), Locale.SPANISH)

        self.assertIs(current_locale(), before)

    def test_primary_terminal_vocabulary_is_in_the_spanish_catalog(self) -> None:
        expected = {
            "Choose": "Elegir",
            "Close": "Cerrar",
            "Continue": "Continuar",
            "Pull only": "Solo recibir",
            "Send ready changes": "Enviar cambios preparados",
            "Search": "Buscar",
            "Work Items": "Elementos de trabajo",
            "New folder": "Nueva carpeta",
            "Move blocked": "Movimiento bloqueado",
        }

        with using_locale(Locale.SPANISH):
            for source, translated in expected.items():
                with self.subTest(source=source):
                    self.assertEqual(translated, translate(source))

    def test_primary_terminal_vocabulary_is_in_the_french_catalog(self) -> None:
        expected = {
            "Choose": "Choisir",
            "Close": "Fermer",
            "Continue": "Continuer",
            "Pull only": "Recevoir uniquement",
            "Send ready changes": "Envoyer les modifications prêtes",
            "Search": "Rechercher",
            "Work Items": "Éléments de travail",
            "New folder": "Nouveau dossier",
            "Move blocked": "Déplacement bloqué",
        }

        with using_locale(Locale.FRENCH):
            for source, translated in expected.items():
                with self.subTest(source=source):
                    self.assertEqual(translated, translate(source))


if __name__ == "__main__":
    unittest.main()
