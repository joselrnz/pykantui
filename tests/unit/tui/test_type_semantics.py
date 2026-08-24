"""Provider-neutral work-item type semantics and theme rendering."""

from __future__ import annotations

import unittest

from rich.style import Style
from textual.app import App
from textual.color import Color

from pykantui.core.work_items import WorkItemColumn
from pykantui.tracker import specs
from pykantui.tui.themes import CUSTOM_THEMES
from pykantui.tui.type_styles import (
    WORK_ITEM_TYPE_CLASSES,
    ItemTypeSemantic,
    TypeThemeVariable,
    item_type_semantic,
    normalize_item_type,
    resolve_type_color,
    type_theme_variable,
    work_item_type_class,
    work_item_type_text,
)


class ItemTypeSemanticTests(unittest.TestCase):
    """Type names carry semantics; provider names do not."""

    def test_normalizes_case_spacing_punctuation_and_compatibility_text(self) -> None:
        self.assertEqual("user story", normalize_item_type("  User_Story  "))
        self.assertEqual("sub task", normalize_item_type("sub-task"))
        self.assertEqual("bug", normalize_item_type("ＢＵＧ"))

    def test_common_provider_vocabulary_maps_to_semantics(self) -> None:
        cases = {
            "Bug": ItemTypeSemantic.DEFECT,
            "production incident": ItemTypeSemantic.DEFECT,
            "Epic": ItemTypeSemantic.EPIC,
            "product initiative": ItemTypeSemantic.EPIC,
            "Feature": ItemTypeSemantic.FEATURE,
            "enhancement request": ItemTypeSemantic.FEATURE,
            "Story": ItemTypeSemantic.STORY,
            "User Story": ItemTypeSemantic.STORY,
            "Sub-task": ItemTypeSemantic.SUBTASK,
            "Child task": ItemTypeSemantic.SUBTASK,
            "Task": ItemTypeSemantic.TASK,
            "Chore": ItemTypeSemantic.TASK,
            "Spike": ItemTypeSemantic.TASK,
            "custom provider value": ItemTypeSemantic.NEUTRAL,
            "": ItemTypeSemantic.NEUTRAL,
        }

        self.assertEqual(
            set(ItemTypeSemantic),
            {item_type_semantic(label) for label in cases},
        )
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertIs(expected, item_type_semantic(label))

    def test_non_text_and_false_positive_values_are_neutral(self) -> None:
        for value in (None, 7, object(), "debug", "taskmaster"):
            with self.subTest(value=value):
                self.assertIs(ItemTypeSemantic.NEUTRAL, item_type_semantic(value))

    def test_each_semantic_uses_a_base_theme_token(self) -> None:
        expected = {
            ItemTypeSemantic.DEFECT: TypeThemeVariable.ERROR,
            ItemTypeSemantic.EPIC: TypeThemeVariable.WARNING,
            ItemTypeSemantic.FEATURE: TypeThemeVariable.SUCCESS,
            ItemTypeSemantic.STORY: TypeThemeVariable.PRIMARY,
            ItemTypeSemantic.SUBTASK: TypeThemeVariable.ACCENT,
            ItemTypeSemantic.TASK: TypeThemeVariable.SECONDARY,
            ItemTypeSemantic.NEUTRAL: TypeThemeVariable.FOREGROUND,
        }

        for semantic, variable in expected.items():
            with self.subTest(semantic=semantic):
                self.assertIs(variable, type_theme_variable(semantic.value))
                self.assertEqual(f"${variable.value}", variable.token)
                self.assertEqual(
                    f"work-item-type-{variable.value}",
                    work_item_type_class(semantic.value),
                )

        self.assertEqual(
            {f"work-item-type-{variable.value}" for variable in TypeThemeVariable},
            WORK_ITEM_TYPE_CLASSES,
        )

    def test_rich_text_is_single_line_and_ellipsis_safe(self) -> None:
        variables = {"error": "#ff0000", "foreground": "#ffffff"}
        rendered = work_item_type_text("Bug\n  🐛", "bug", variables)

        self.assertEqual("Bug 🐛", rendered.plain)
        self.assertTrue(rendered.no_wrap)
        self.assertEqual("ellipsis", rendered.overflow)
        self.assertIsInstance(rendered.style, Style)
        assert isinstance(rendered.style, Style)
        self.assertEqual(Color.parse("#ff0000").rich_color, rendered.style.color)

    def test_bad_or_missing_theme_values_fall_back_safely(self) -> None:
        expected = Color.parse("ansi_default")
        self.assertEqual(expected, resolve_type_color("bug", {"error": "not-a-colour"}))
        self.assertEqual(expected, resolve_type_color("unknown", {}))


class ProviderTypeAvailabilityTests(unittest.TestCase):
    """All bundled providers declare Type from their field contract."""

    def test_all_ten_provider_specs_have_the_expected_type_capability(self) -> None:
        providers = {spec.name: spec for spec in specs()}
        self.assertEqual(
            {
                "asana",
                "clickup",
                "forgejo",
                "github",
                "jira",
                "linear",
                "monday",
                "plane",
                "shortcut",
                "trello",
            },
            set(providers),
        )
        expected = {
            "asana": False,
            "clickup": True,
            "forgejo": False,
            "github": True,
            "jira": True,
            "linear": False,
            "monday": False,
            # Plane returns a raw ``type_id`` but its usable type directory is
            # not available on the free API tier. Keep the opaque id in
            # metadata without advertising a misleading Type field.
            "plane": False,
            "shortcut": True,
            "trello": False,
        }

        for name, available in expected.items():
            with self.subTest(provider=name):
                columns = providers[name].available_table_fields({})
                self.assertEqual(available, WorkItemColumn.TYPE in columns)

        monday_columns = providers["monday"].available_table_fields(
            {"type_column": "type"}
        )
        self.assertIn(WorkItemColumn.TYPE, monday_columns)


class ItemTypeThemeMatrixTests(unittest.TestCase):
    """Every selectable theme resolves every semantic type colour."""

    def test_every_semantic_resolves_in_every_available_theme(self) -> None:
        app: App[None] = App()
        for theme in CUSTOM_THEMES:
            app.register_theme(theme)
        themes = app.available_themes
        self.assertEqual(24, len(themes))

        for theme_name, theme in themes.items():
            variables = theme.to_color_system().generate()
            for semantic in ItemTypeSemantic:
                with self.subTest(theme=theme_name, semantic=semantic):
                    variable = type_theme_variable(semantic.value)
                    expected = Color.parse(variables[variable.value])
                    self.assertEqual(
                        expected,
                        resolve_type_color(semantic.value, variables),
                    )


if __name__ == "__main__":
    unittest.main()
