"""Responsive comment composer and rounded action-button appearance."""

from __future__ import annotations

import unittest

from textual.color import Color
from textual.widgets import Button, TextArea

from pykantui.models import BoardLayout, Edges
from pykantui.pages.sync import SyncProgressScreen
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace.models import SyncReport
from tests.integration.tui.test_comments_ui import CommentsBackend, settle, two_comments


def _border_kinds(button: Button) -> set[str]:
    border = button.styles.border
    return {str(edge[0]) for edge in (border.top, border.right, border.bottom, border.left)}


def _theme_color(app: KanbanApp, token: str) -> Color:
    return Color.parse(app.theme_variables[token])


class CommentComposerLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_split_composer_is_tall_while_compact_split_stays_reachable(self) -> None:
        for size, minimum, maximum in (((150, 40), 8, None), ((96, 18), 3, 4)):
            with self.subTest(size=size):
                app = KanbanApp(CommentsBackend(comments=two_comments()), confirm_moves=False)
                async with app.run_test(size=size) as pilot:
                    await settle(pilot)
                    app.set_board_layout(BoardLayout.SPLIT)
                    await settle(pilot)
                    view = app.query_one(WorkItemsView)
                    view.action_focus_tab("comments")
                    await settle(pilot)

                    pane = view.query_one("#work-item-comments-pane")
                    draft = view.query_one("#work-item-comment-draft", TextArea)
                    add = view.query_one("#work-item-comment-add-local", Button)
                    self.assertGreaterEqual(draft.region.height, minimum)
                    if maximum is not None:
                        self.assertLessEqual(draft.region.height, maximum)
                    self.assertLessEqual(draft.region.bottom, pane.content_region.bottom)
                    self.assertLessEqual(add.region.bottom, pane.content_region.bottom)
                    draft.load_text("\n".join(f"Comment line {line}" for line in range(20)))
                    await pilot.pause()
                    self.assertGreater(draft.max_scroll_y, 0)
                    self.assertEqual(1, draft.styles.scrollbar_size_vertical)


class RoundedActionAppearanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_comment_actions_are_transparent_round_pills_in_both_edge_modes(self) -> None:
        for edges in (Edges.ROUND, Edges.SQUARE):
            with self.subTest(edges=edges):
                backend = CommentsBackend(comments=two_comments())
                backend.config.edges = edges
                app = KanbanApp(backend, confirm_moves=False)
                async with app.run_test(size=(150, 40)) as pilot:
                    await settle(pilot)
                    app.set_board_layout(BoardLayout.SPLIT)
                    await settle(pilot)
                    view = app.query_one(WorkItemsView)
                    view.action_focus_tab("comments")
                    await settle(pilot)

                    refresh = view.query_one("#work-item-comment-refresh", Button)
                    add = view.query_one("#work-item-comment-add-local", Button)
                    view.query_one("#work-item-comment-draft", TextArea).load_text("Ready to add")
                    await pilot.pause()
                    self.assertFalse(add.disabled)
                    for theme in sorted(app.available_themes):
                        with self.subTest(edges=edges, theme=theme):
                            app.theme = theme
                            await pilot.pause()
                            for button in (refresh, add):
                                self.assertEqual({"round"}, _border_kinds(button))
                                self.assertEqual(0.0, button.styles.background.a)
                                self.assertGreaterEqual(button.region.height, 3)
                            self.assertEqual(_theme_color(app, "border"), refresh.styles.border.top[1])
                            self.assertEqual(_theme_color(app, "success"), add.styles.border.top[1])

                    await pilot.hover(add)
                    await pilot.pause()
                    self.assertEqual(0.0, add.styles.background.a)
                    self.assertTrue(add.styles.text_style.bold)

    async def test_sync_close_is_a_transparent_round_pill_even_with_square_edges(self) -> None:
        backend = CommentsBackend()
        backend.config.edges = Edges.SQUARE
        app = KanbanApp(backend, confirm_moves=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot)
            screen = SyncProgressScreen("Jira", "JPT")
            app.push_screen(screen)
            await pilot.pause()
            screen.finish_success(SyncReport())
            await pilot.pause()

            close = screen.query_one("#sync-progress-close", Button)
            self.assertFalse(close.disabled)
            for theme in sorted(app.available_themes):
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.pause()
                    self.assertEqual({"round"}, _border_kinds(close))
                    self.assertEqual(_theme_color(app, "accent-lighten-3"), close.styles.border.top[1])
                    self.assertEqual(0.0, close.styles.background.a)
                    self.assertGreaterEqual(close.region.width, 14)

            await pilot.hover(close)
            await pilot.pause()
            self.assertEqual(0.0, close.styles.background.a)
            self.assertTrue(close.styles.text_style.bold)


if __name__ == "__main__":
    unittest.main()
