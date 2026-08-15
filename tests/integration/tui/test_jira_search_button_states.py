"""The provider-query action stays a legible pill in every appearance."""

from __future__ import annotations

import unittest

from rich.cells import cell_len
from textual.color import Color
from textual.pilot import Pilot
from textual.widgets import Button, Input

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.models import Edges
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker import get
from pykantui.tracker.filter_fields import FilterFieldSpec
from pykantui.tui.app import KanbanApp
from pykantui.tui.glyphs import SEARCH_GLYPH


class QueryButtonBackend(JsonBackend):
    """Small provider-shaped backend with no network or filesystem writes."""

    supports_query = True

    def __init__(
        self,
        provider: str = "jira",
        *,
        edges: Edges = Edges.ROUND,
        runnable: bool = True,
    ) -> None:
        super().__init__(
            config=BoardConfig(
                columns=[ColumnConfig(column_id=1, name="To Do", position=0)],
                reset_column=1,
                edges=edges,
            )
        )
        self.spec = get(provider).spec
        self.runnable = runnable
        self.queries: list[str] = []

    def provider_filter_fields(self) -> tuple[FilterFieldSpec, ...]:
        return self.spec.filter_fields({})

    def can_run_query(self) -> bool:
        return self.runnable and bool(self.spec.capabilities.query_language)

    def run_query(self, query: str) -> None:
        self.queries.append(query)


async def expand(pilot: Pilot[None]) -> None:
    await pilot.pause()
    await pilot.press("f2", "f2")
    await pilot.pause()


def theme_color(app: KanbanApp, token: str) -> Color:
    """Resolve the opaque named/hex tokens asserted by this contract."""
    return Color.parse(app.theme_variables[token])


def contrast(left: Color, right: Color) -> float:
    """WCAG contrast for non-ANSI theme colors."""

    def luminance(color: Color) -> float:
        channels: list[float] = []
        for channel in (color.r, color.g, color.b):
            value = channel / 255
            channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    brighter, darker = sorted((luminance(left), luminance(right)), reverse=True)
    return (brighter + 0.05) / (darker + 0.05)


class JiraSearchButtonCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_query_capable_providers_mount_an_enabled_search_action(self) -> None:
        cases = (
            (QueryButtonBackend("jira"), 1, False),
            (QueryButtonBackend("jira", runnable=False), 1, True),
            (QueryButtonBackend("asana"), 0, None),
            (JsonBackend(), 0, None),
        )

        for backend, expected_count, expected_disabled in cases:
            with self.subTest(backend=type(backend).__name__, provider=getattr(backend, "spec", None)):
                app = KanbanApp(backend, confirm_moves=False)
                async with app.run_test(size=(160, 42)) as pilot:
                    await expand(pilot)
                    buttons = app.menu_bar.query("#filter-search")
                    queries = app.menu_bar.query("#filter-query")

                    self.assertEqual(expected_count, len(buttons))
                    self.assertEqual(expected_count, len(queries))
                    if expected_disabled is not None:
                        self.assertIs(expected_disabled, buttons.first(Button).disabled)
                        self.assertIs(expected_disabled, queries.first(Input).disabled)

    async def test_search_glyph_is_one_cell_and_the_label_fits_its_button(self) -> None:
        app = KanbanApp(QueryButtonBackend(), confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await expand(pilot)
            button = app.menu_bar.query_one("#filter-search", Button)
            label = str(button.label)

            self.assertEqual("⌕", SEARCH_GLYPH)
            self.assertEqual(1, cell_len(SEARCH_GLYPH))
            self.assertTrue(label.startswith(f"{SEARCH_GLYPH} "))
            self.assertLessEqual(cell_len(label), button.content_region.width)

    async def test_real_click_and_enter_each_execute_exactly_one_query(self) -> None:
        for gesture in ("click", "enter"):
            with self.subTest(gesture=gesture):
                backend = QueryButtonBackend()
                app = KanbanApp(backend, confirm_moves=False)
                async with app.run_test(size=(160, 42)) as pilot:
                    await expand(pilot)
                    query = app.menu_bar.query_one("#filter-query", Input)
                    query.value = 'status = "In Progress"'
                    if gesture == "click":
                        await pilot.click("#filter-search")
                    else:
                        query.focus()
                        await pilot.press("enter")
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                self.assertEqual(['status = "In Progress"'], backend.queries)


class JiraSearchButtonAppearanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_and_square_edge_modes_apply_to_every_theme(self) -> None:
        for edges in (Edges.ROUND, Edges.SQUARE):
            app = KanbanApp(QueryButtonBackend(edges=edges), confirm_moves=False)
            async with app.run_test(size=(160, 42)) as pilot:
                await expand(pilot)
                button = app.menu_bar.query_one("#filter-search", Button)
                for theme in sorted(app.available_themes):
                    with self.subTest(edges=edges, theme=theme):
                        app.theme = theme
                        await pilot.pause()
                        borders = button.styles.border
                        self.assertEqual(
                            {"round"},
                            {str(edge[0]) for edge in (borders.top, borders.right, borders.bottom, borders.left)},
                        )
                        self.assertEqual(theme_color(app, "accent"), borders.top[1])
                        self.assertEqual(0.0, button.styles.background.a)

    async def test_normal_hover_focus_and_pressed_states_are_theme_legible(self) -> None:
        app = KanbanApp(QueryButtonBackend(), confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await expand(pilot)
            button = app.menu_bar.query_one("#filter-search", Button)
            query = app.menu_bar.query_one("#filter-query", Input)

            for theme in sorted(app.available_themes):
                with self.subTest(theme=theme):
                    app.theme = theme
                    query.focus()
                    await pilot.hover(query)
                    await pilot.pause()

                    self.assertEqual(theme_color(app, "accent"), button.styles.border.top[1])
                    self.assertEqual(theme_color(app, "text-accent"), button.styles.color)
                    self.assertEqual(0.0, button.styles.background.a)
                    self.assertTrue(button.styles.text_style.bold)
                    if not theme.startswith("ansi-"):
                        self.assertGreaterEqual(
                            contrast(button.styles.color, theme_color(app, "background")),
                            3.0,
                        )

                    await pilot.hover(button)
                    await pilot.pause()
                    self.assertEqual(theme_color(app, "accent-lighten-3"), button.styles.border.top[1])
                    self.assertEqual(theme_color(app, "text-accent"), button.styles.color)
                    self.assertEqual(0.0, button.styles.background.a)

                    await pilot.hover(query)
                    button.focus()
                    await pilot.pause()
                    self.assertEqual(theme_color(app, "accent-lighten-3"), button.styles.border.top[1])
                    self.assertEqual(theme_color(app, "text-accent"), button.styles.color)
                    self.assertEqual(0.0, button.styles.background.a)

                    button.add_class("-active")
                    await pilot.pause()
                    self.assertEqual(theme_color(app, "accent"), button.styles.border.top[1])
                    self.assertEqual(theme_color(app, "text-accent"), button.styles.color)
                    self.assertEqual(0.0, button.styles.background.a)
                    self.assertTrue(button.styles.text_style.bold)
                    self.assertTrue(button.styles.text_style.reverse)
                    button.remove_class("-active")

    async def test_disabled_state_cannot_be_hover_highlighted_or_executed(self) -> None:
        backend = QueryButtonBackend(runnable=False)
        app = KanbanApp(backend, confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await expand(pilot)
            button = app.menu_bar.query_one("#filter-search", Button)
            for theme in sorted(app.available_themes):
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.hover(button)
                    await pilot.pause()
                    self.assertTrue(button.disabled)
                    self.assertEqual("round", str(button.styles.border.top[0]))
                    self.assertEqual(theme_color(app, "border"), button.styles.border.top[1])
                    self.assertEqual(0.0, button.styles.background.a)
                    self.assertLess(button.styles.opacity, 1.0)

            await pilot.click("#filter-search")
            await pilot.pause()

        self.assertEqual([], backend.queries)


if __name__ == "__main__":
    unittest.main()
