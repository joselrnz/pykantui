"""Real keyboard journey for deleting only an unsent provider draft."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pykantui.models import Task
from pykantui.pages.menu import ContextMenuScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker.registry import get
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.card import TaskCard
from pykantui.workspace import layout
from pykantui.workspace.sync import sync
from tests.integration.sync.test_push import PROJECT, TODO, RecordingProvider, issue
from tests.integration.tui.test_mouse_ui import choose


class ProviderDraftDeleteJourneyTests(unittest.IsolatedAsyncioTestCase):
    async def test_d_cancel_keeps_draft_and_makes_no_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            provider = RecordingProvider([])
            provider.spec = get("jira").spec  # type: ignore[misc]
            sync(workspace, provider, PROJECT, push_edits=False, commit=False)
            backend = ProviderBackend(workspace, provider, PROJECT)
            created = backend.create_task(Task(task_id=999, title="Keep this draft", column_id=1))
            self.assertTrue(created.ok, created.message)
            provider.updates.clear()
            provider.moves.clear()
            provider.fetches.clear()
            app = KanbanApp(backend, confirm_moves=False)

            async with app.run_test(size=(120, 32)) as pilot:
                await pilot.pause()
                card = next(iter(app.query(TaskCard)))
                card.focus()
                await pilot.press("d")
                await pilot.pause()
                await choose(pilot, "Cancel")
                await pilot.pause()

                self.assertEqual(["Keep this draft"], [item.title for item in backend.get_tasks()])
                self.assertEqual([], list(layout.trash_dir(workspace).rglob("*.md")))
                self.assertEqual(
                    (0, 0, 0),
                    (len(provider.updates), len(provider.moves), len(provider.fetches)),
                )

    async def test_d_confirms_quarantines_refreshes_selection_and_never_calls_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            provider = RecordingProvider([issue("JPT-1", TODO, title="Synced survivor")])
            provider.spec = get("jira").spec  # type: ignore[misc]
            sync(workspace, provider, PROJECT, push_edits=False, commit=False)
            backend = ProviderBackend(workspace, provider, PROJECT)
            created = backend.create_task(Task(task_id=999, title="Delete this unsent draft", column_id=1))
            self.assertTrue(created.ok, created.message)
            provider.updates.clear()
            provider.moves.clear()
            provider.fetches.clear()
            app = KanbanApp(backend, confirm_moves=False)

            async with app.run_test(size=(120, 32)) as pilot:
                await pilot.pause()
                draft_card = next(
                    card for card in app.query(TaskCard) if card.task_.title == "Delete this unsent draft"
                )
                synced_card = next(card for card in app.query(TaskCard) if "Synced survivor" in card.task_.title)
                self.assertIs(draft_card.check_action("delete", ()), True)
                self.assertIs(synced_card.check_action("delete", ()), False)
                draft_card.focus()

                await pilot.press("d")
                await pilot.pause()

                menu = app.screen
                self.assertIsInstance(menu, ContextMenuScreen)
                assert isinstance(menu, ContextMenuScreen)
                self.assertIn("Delete this unsent draft", menu.menu_title)
                self.assertEqual((0, 0, 0), (len(provider.updates), len(provider.moves), len(provider.fetches)))
                self.assertIn(
                    "Delete this unsent draft",
                    [item.title for item in backend.get_tasks()],
                )

                await choose(pilot, "Delete cards")
                await pilot.pause()

                self.assertNotIn(
                    "Delete this unsent draft",
                    [item.title for item in backend.get_tasks()],
                )
                self.assertEqual(1, len(list(layout.trash_dir(workspace).rglob("*.md"))))
                self.assertEqual(1, len(app.query(TaskCard)))
                self.assertTrue(next(iter(app.query(TaskCard))).has_focus)
                self.assertEqual((0, 0, 0), (len(provider.updates), len(provider.moves), len(provider.fetches)))


if __name__ == "__main__":
    unittest.main()
