"""Every Kanban card has a quiet frame and clear interaction states."""

from __future__ import annotations

import unittest

from textual.color import Color

from pykantui.models import Edges, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from tests.integration.tui.test_board_tui import settle


def _theme_color(app: KanbanApp, token: str) -> Color:
    return Color.parse(app.theme_variables[token])


def _border_kinds(card: TaskCard) -> set[str]:
    border = card.styles.border
    return {str(edge[0]) for edge in (border.top, border.right, border.bottom, border.left)}


def _quiet_border(app: KanbanApp) -> Color:
    color = _theme_color(app, "border")
    # ANSI colors are palette indexes; terminals can't blend their alpha.
    return color if color.ansi is not None else color.with_alpha(0.2)


def _backend(edges: Edges, *, count: int = 4) -> JsonBackend:
    backend = JsonBackend()
    backend.config.edges = edges
    for number in range(1, count + 1):
        backend.create_task(Task(task_id=number, title=f"Card {number:02d}", column_id=1))
    return backend


class CardOutlineAppearanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_focus_hover_blocked_and_flash_are_distinct_in_every_theme(self) -> None:
        for edges, expected_kind in ((Edges.ROUND, "round"), (Edges.SQUARE, "solid")):
            with self.subTest(edges=edges):
                app = KanbanApp(_backend(edges), confirm_moves=False)
                async with app.run_test(size=(140, 40)) as pilot:
                    await settle(pilot)
                    cards = list(app.query(TaskCard))
                    focused, resting, blocked, flashed = cards
                    focused.focus()
                    blocked.add_class("blocked")
                    flashed.add_class("flash")
                    await pilot.pause()

                    for theme in sorted(app.available_themes):
                        with self.subTest(edges=edges, theme=theme):
                            app.theme = theme
                            await pilot.hover(app.board)
                            await pilot.pause()

                            self.assertEqual({expected_kind}, _border_kinds(resting))
                            self.assertEqual(_quiet_border(app), resting.styles.border.top[1])
                            self.assertEqual(0.0, resting.styles.background.a)
                            self.assertGreaterEqual(resting.region.width, 20)
                            self.assertGreaterEqual(resting.content_region.height, 2)
                            self.assertEqual(_theme_color(app, "accent"), focused.styles.border.top[1])
                            self.assertEqual(_theme_color(app, "error"), blocked.styles.border.top[1])
                            self.assertEqual(_theme_color(app, "warning"), flashed.styles.border.top[1])

                            await pilot.hover(resting)
                            await pilot.pause()
                            self.assertEqual(_theme_color(app, "primary"), resting.styles.border.top[1])

    async def test_twenty_five_cards_all_keep_the_subtle_frame(self) -> None:
        app = KanbanApp(_backend(Edges.ROUND, count=25), confirm_moves=False)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(pilot)
            await pilot.hover(app.board)
            await pilot.pause()

            cards = list(app.query(TaskCard))
            self.assertEqual(25, len(cards))
            expected = _quiet_border(app)
            for card in cards:
                with self.subTest(card=card.task_.task_id):
                    if card.has_focus:
                        self.assertEqual(_theme_color(app, "accent"), card.styles.border.top[1])
                    else:
                        self.assertEqual({"round"}, _border_kinds(card))
                        self.assertEqual(expected, card.styles.border.top[1])
                        self.assertEqual(0.0, card.styles.background.a)


if __name__ == "__main__":
    unittest.main()
