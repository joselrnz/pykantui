"""Provider-aware fields and semantic colors in card popup screens."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Input, Select

from pykantui.models import BoardLayout
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.edit import TaskEditScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker import get
from pykantui.tui.app import KanbanApp
from pykantui.tui.status_styles import (
    resolve_status_color,
    workflow_status_class,
)
from pykantui.tui.type_styles import resolve_type_color, work_item_type_class
from tests.integration.sync.test_push import (
    DONE,
    PROJECT,
    REVIEW,
    TODO,
    RecordingProvider,
    issue,
)


def provider_backend(
    workspace: Path,
    provider_name: str,
    *,
    issue_type: str = "Bug",
) -> ProviderBackend:
    """Build an offline provider workspace with an intentionally populated Type."""
    provider = RecordingProvider([issue("K-1", TODO, issue_type=issue_type)])
    provider.spec = get(provider_name).spec  # type: ignore[misc]
    from pykantui.workspace.sync import sync

    sync(workspace, provider, PROJECT, push_edits=False, commit=False)
    return ProviderBackend(workspace, provider, PROJECT)


class PopupProviderFieldTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_type_hidden(self, provider_name: str, layout: BoardLayout) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = provider_backend(Path(directory), provider_name)
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=(150, 42)) as pilot:
                await pilot.pause()
                app.set_board_layout(layout)
                await pilot.pause()

                await pilot.press("v")
                await pilot.pause()
                self.assertIsInstance(app.screen, TaskDetailScreen)
                self.assertEqual(0, len(app.screen.query("#detail-issue-type")))
                self.assertTrue(app.screen.query("#detail-status"))

                await pilot.press("e")
                await pilot.pause()
                self.assertEqual(0, len(app.screen.query("#detail-issue-type")))
                await pilot.press("escape")
                await pilot.pause()

                await pilot.press("n")
                await pilot.pause()
                self.assertIsInstance(app.screen, TaskEditScreen)
                self.assertEqual(0, len(app.screen.query("#edit-issue-type")))
                self.assertTrue(app.screen.query("#edit-column"))
                await pilot.press("escape")

    async def test_asana_hides_unsupported_type_in_kanban_popups(self) -> None:
        await self._assert_type_hidden("asana", BoardLayout.KANBAN)

    async def test_plane_hides_unavailable_type_in_rows_popups(self) -> None:
        await self._assert_type_hidden("plane", BoardLayout.ROWS)


class PopupSemanticThemeTests(unittest.IsolatedAsyncioTestCase):
    async def test_jira_detail_and_new_edit_semantics_follow_every_theme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = provider_backend(Path(directory), "jira")
            app = KanbanApp(backend, confirm_moves=False)
            async with app.run_test(size=(150, 42)) as pilot:
                await pilot.pause()
                themes = tuple(sorted(app.available_themes))
                self.assertEqual(24, len(themes))

                await pilot.press("v")
                await pilot.pause()
                self.assertIsInstance(app.screen, TaskDetailScreen)
                detail_status = app.screen.query_one("#detail-status", Select)
                detail_type = app.screen.query_one("#detail-issue-type", Input)
                self.assertTrue(detail_status.disabled)
                self.assertTrue(detail_type.disabled)
                self.assertTrue(
                    detail_status.has_class(workflow_status_class(backend.column_group(1)))
                )
                self.assertTrue(detail_type.has_class(work_item_type_class("Bug")))

                await pilot.press("e")
                await pilot.pause()
                detail_type.value = "Epic"
                done_id = next(
                    column.column_id
                    for column in backend.get_columns()
                    if column.name == DONE.name
                )
                detail_status.value = str(done_id)
                await pilot.pause()
                self.assertTrue(detail_type.has_class(work_item_type_class("Epic")))
                self.assertTrue(
                    detail_status.has_class(workflow_status_class(backend.column_group(done_id)))
                )

                for theme in themes:
                    with self.subTest(screen="detail", theme=theme):
                        app.theme = theme
                        await pilot.pause()
                        self.assertEqual(
                            resolve_type_color("Epic", app.theme_variables).rich_color,
                            detail_type.styles.border.top[1].rich_color,
                        )
                        self.assertEqual(
                            resolve_status_color(
                                backend.column_group(done_id), app.theme_variables
                            ).rich_color,
                            detail_status.styles.border.top[1].rich_color,
                        )

                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("n")
                await pilot.pause()
                self.assertIsInstance(app.screen, TaskEditScreen)
                edit_status = app.screen.query_one("#edit-column", Select)
                edit_type = app.screen.query_one("#edit-issue-type", Input)
                edit_type.value = "Bug"
                review_id = next(
                    column.column_id
                    for column in backend.get_columns()
                    if column.name == REVIEW.name
                )
                edit_status.value = str(review_id)
                await pilot.pause()
                self.assertTrue(edit_type.has_class(work_item_type_class("Bug")))
                self.assertTrue(
                    edit_status.has_class(workflow_status_class(backend.column_group(review_id)))
                )

                for theme in themes:
                    with self.subTest(screen="new", theme=theme):
                        app.theme = theme
                        await pilot.pause()
                        self.assertEqual(
                            resolve_type_color("Bug", app.theme_variables).rich_color,
                            edit_type.styles.border.top[1].rich_color,
                        )
                        self.assertEqual(
                            resolve_status_color(
                                backend.column_group(review_id), app.theme_variables
                            ).rich_color,
                            edit_status.styles.border.top[1].rich_color,
                        )

                self.assertIn("ansi-dark", themes)
                self.assertIn("ansi-light", themes)


if __name__ == "__main__":
    unittest.main()
