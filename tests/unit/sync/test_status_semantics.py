"""Provider-neutral workflow groups and their theme-aware presentation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rich.cells import cell_len
from textual.color import Color
from textual.theme import BUILTIN_THEMES, Theme

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.sync.jsonstore import JsonBackend
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.models import ColumnGroup, RemoteColumn
from pykantui.tracker.registry import specs
from pykantui.tui.status_styles import (
    StatusThemeVariable,
    resolve_status_color,
    status_theme_variable,
    workflow_status_text,
)
from pykantui.tui.themes import CUSTOM_THEMES
from tests.edge_cases.providers.load_fixtures import MatrixProvider, project_for


class LocalColumnGroupTests(unittest.TestCase):
    """Local boards infer semantics without provider-specific vocabulary."""

    def test_every_group_can_be_inferred_from_a_local_column_name(self) -> None:
        cases = {
            "Backlog": ColumnGroup.BACKLOG,
            "To Do": ColumnGroup.TODO,
            "Doing": ColumnGroup.STARTED,
            "Code Review": ColumnGroup.REVIEW,
            "Done": ColumnGroup.DONE,
            "Cancelled": ColumnGroup.CANCELLED,
            "Maybe Laterish": ColumnGroup.UNKNOWN,
        }
        config = BoardConfig(
            columns=[
                ColumnConfig(column_id=index, name=name, position=index)
                for index, name in enumerate(cases, start=1)
            ]
        )
        backend = JsonBackend(config=config)

        for column_id, expected in enumerate(cases.values(), start=1):
            with self.subTest(group=expected):
                self.assertIs(expected, backend.column_group(column_id))

    def test_explicit_local_roles_beat_ambiguous_names(self) -> None:
        config = BoardConfig(
            columns=[
                ColumnConfig(column_id=10, name="Queue", position=0),
                ColumnConfig(column_id=20, name="Queue", position=1),
                ColumnConfig(column_id=30, name="Queue", position=2),
            ],
            reset_column=10,
            start_column=20,
            finish_column=30,
        )
        backend = JsonBackend(config=config)

        self.assertIs(ColumnGroup.TODO, backend.column_group(10))
        self.assertIs(ColumnGroup.STARTED, backend.column_group(20))
        self.assertIs(ColumnGroup.DONE, backend.column_group(30))

    def test_conflicting_roles_and_missing_columns_fail_closed(self) -> None:
        config = BoardConfig(
            columns=[ColumnConfig(column_id=1, name="Done", position=0)],
            reset_column=1,
            finish_column=1,
        )
        backend = JsonBackend(config=config)

        self.assertIs(ColumnGroup.UNKNOWN, backend.column_group(1))
        self.assertIs(ColumnGroup.UNKNOWN, backend.column_group(404))


class ProviderColumnGroupTests(unittest.TestCase):
    """Provider boards preserve the semantic group supplied by their adapter."""

    def test_remote_columns_normalize_strings_to_the_shared_enum(self) -> None:
        column = RemoteColumn(column_id="1", name="To Do", group="todo")  # type: ignore[arg-type]

        self.assertIs(ColumnGroup.TODO, column.group)
        self.assertEqual("todo", column.model_dump(mode="json")["group"])

    def test_every_remote_group_round_trips_through_the_backend(self) -> None:
        backend = object.__new__(ProviderBackend)
        backend._column_ids = {  # noqa: SLF001 - isolated backend-contract fixture
            index: RemoteColumn(column_id=str(index), name=group.value, group=group)
            for index, group in enumerate(ColumnGroup, start=1)
        }

        for index, expected in enumerate(ColumnGroup, start=1):
            with self.subTest(group=expected):
                self.assertIs(expected, backend.column_group(index))
        self.assertIs(ColumnGroup.UNKNOWN, backend.column_group(404))

    def test_every_built_in_provider_uses_the_same_backend_contract_offline(self) -> None:
        provider_specs = specs()
        self.assertEqual(
            {"asana", "clickup", "forgejo", "github", "jira", "linear", "monday", "plane", "shortcut", "trello"},
            {item.name for item in provider_specs},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for provider_spec in provider_specs:
                provider = MatrixProvider(provider_spec, [])
                try:
                    backend = ProviderBackend(root / provider_spec.name, provider, project_for(provider_spec))
                    with self.subTest(provider=provider_spec.name):
                        self.assertIs(ColumnGroup.TODO, backend.column_group(1))
                        self.assertIs(ColumnGroup.DONE, backend.column_group(2))
                finally:
                    provider.close()

    def test_an_invalid_legacy_plugin_group_fails_closed(self) -> None:
        backend = object.__new__(ProviderBackend)
        backend._column_ids = {  # noqa: SLF001 - bypass validation to emulate an old plugin
            1: RemoteColumn.model_construct(column_id="old", name="Old", group="sideways")
        }

        self.assertIs(ColumnGroup.UNKNOWN, backend.column_group(1))


class StatusThemeTests(unittest.TestCase):
    """Workflow colours resolve from every registered Textual theme."""

    THEMES: tuple[Theme, ...] = (*BUILTIN_THEMES.values(), *CUSTOM_THEMES)

    @staticmethod
    def variables(theme: Theme) -> dict[str, str]:
        return theme.to_color_system().generate() | theme.variables

    def test_each_workflow_group_has_a_semantic_theme_variable(self) -> None:
        expected = {
            ColumnGroup.BACKLOG: StatusThemeVariable.PRIMARY,
            ColumnGroup.TODO: StatusThemeVariable.WARNING,
            ColumnGroup.STARTED: StatusThemeVariable.SECONDARY,
            ColumnGroup.REVIEW: StatusThemeVariable.ACCENT,
            ColumnGroup.DONE: StatusThemeVariable.SUCCESS,
            ColumnGroup.CANCELLED: StatusThemeVariable.ERROR,
            ColumnGroup.UNKNOWN: StatusThemeVariable.FOREGROUND,
        }

        for group, variable in expected.items():
            with self.subTest(group=group):
                self.assertIs(variable, status_theme_variable(group))
                self.assertEqual(f"${variable.value}", variable.token)

    def test_invalid_groups_use_the_neutral_unknown_style(self) -> None:
        self.assertIs(StatusThemeVariable.FOREGROUND, status_theme_variable("sideways"))

    def test_all_groups_resolve_in_every_registered_theme(self) -> None:
        for theme in self.THEMES:
            variables = self.variables(theme)
            for group in ColumnGroup:
                with self.subTest(theme=theme.name, group=group):
                    self.assertIsInstance(resolve_status_color(group, variables), Color)

    def test_missing_or_malformed_theme_values_fall_back_to_the_terminal_default(self) -> None:
        self.assertEqual(Color.parse("ansi_default"), resolve_status_color(ColumnGroup.DONE, {}))
        self.assertEqual(
            Color.parse("ansi_default"),
            resolve_status_color(ColumnGroup.DONE, {"success": "not-a-colour"}),
        )

    def test_rich_status_text_is_width_safe_and_preserves_the_label(self) -> None:
        label = "進行中 — a deliberately long status"
        rendered = workflow_status_text(
            label,
            ColumnGroup.STARTED,
            self.variables(next(theme for theme in self.THEMES if theme.name == "cyberpunk")),
        )

        self.assertEqual(label, rendered.plain)
        self.assertEqual(cell_len(label), cell_len(rendered.plain))
        self.assertTrue(rendered.no_wrap)
        self.assertEqual("ellipsis", rendered.overflow)
        self.assertIsNotNone(rendered.style)


if __name__ == "__main__":
    unittest.main()
