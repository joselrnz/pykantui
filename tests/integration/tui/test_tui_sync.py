"""The provider-backed board keeps local reload and remote sync separate.

These are user journeys rather than implementation tests:

* an editor changes Markdown and Reload shows that exact local version;
* a board move changes Markdown first and leaves the provider untouched;
* Sync previews the outbound work and sends it only after approval.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from textual.color import Color
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from pykantui.models import BoardLayout, Task
from pykantui.pages.detail import TaskDetailScreen
from pykantui.pages.edit import TaskEditScreen
from pykantui.pages.sync import SyncConfirmScreen
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker import get
from pykantui.tracker.errors import ProviderError
from pykantui.tui.app import KanbanApp
from pykantui.tui.widgets.work_items import WorkItemsView
from pykantui.workspace import markdown
from pykantui.workspace.status import SyncStatus
from tests.integration.sync.test_push import DOING, PROJECT, TODO, RecordingProvider, issue


class ProviderBoardJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.provider = RecordingProvider([issue("K-1", TODO)])

        from pykantui.workspace.sync import sync

        sync(
            self.workspace,
            self.provider,
            PROJECT,
            push_edits=False,
            commit=False,
        )
        self.backend = ProviderBackend(self.workspace, self.provider, PROJECT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def card(self) -> Task:
        return next(task for task in self.backend.get_tasks() if task.metadata["key"] == "K-1")

    def markdown(self) -> Path:
        return next(self.workspace.rglob("K-1.md"))

    def test_reload_reads_local_markdown_without_contacting_provider(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace("title: Title K-1", "title: Edited on disk")
            .replace("Body K-1", "Body edited on disk"),
            encoding="utf-8",
        )
        self.provider.fetches.clear()
        self.provider.updates.clear()
        self.provider.moves.clear()

        self.backend.reload_local()

        card = self.card()
        self.assertIn("Edited on disk", card.title)
        self.assertEqual("Body edited on disk", card.description)
        self.assertEqual(SyncStatus.EDITED.value, card.metadata["sync_status"])
        self.assertEqual([], self.provider.fetches)
        self.assertEqual([], self.provider.updates)
        self.assertEqual([], self.provider.moves)

    def test_invalid_markdown_is_visible_but_never_applied_as_an_edit(self) -> None:
        path = self.markdown()
        original_title = self.card().title
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Title K-1",
                "title: Title K-1\nlabels: backend",
            ),
            encoding="utf-8",
        )

        self.backend.reload_local()

        card = self.card()
        self.assertEqual(SyncStatus.INVALID.value, card.metadata["sync_status"])
        self.assertEqual(original_title, card.title)
        self.assertIn("invalid Markdown", " ".join(self.backend.warnings()))
        self.assertEqual([], self.provider.updates)

    def test_tui_move_moves_markdown_but_does_not_send_to_provider(self) -> None:
        card = self.card()
        target = next(column for column in self.backend.get_columns() if column.name == DOING.name)

        result = self.backend.move_task(card, target.column_id)

        self.assertTrue(result.ok, result.message)
        self.assertEqual([], self.provider.moves)
        self.assertEqual([], self.provider.updates)
        moved = next(self.workspace.rglob("K-1.md"))
        self.assertEqual("in-progress", moved.parent.name)
        self.assertEqual(SyncStatus.EDITED.value, self.card().metadata["sync_status"])

    def test_tui_move_refuses_a_symlinked_target_column(self) -> None:
        card = self.card()
        target = next(column for column in self.backend.get_columns() if column.name == DOING.name)
        target_folder = self.workspace / "rec/projects/JPT/in-progress"
        with tempfile.TemporaryDirectory() as outside_name:
            outside = Path(outside_name)
            target_folder.rmdir()
            try:
                target_folder.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            result = self.backend.move_task(card, target.column_id)

            self.assertFalse(result.ok)
            self.assertIn("refusing workspace path", result.message)
            self.assertEqual([], list(outside.iterdir()))
            self.assertEqual("to-do", self.markdown().parent.name)

    def test_failed_local_git_move_does_not_create_a_duplicate_card_file(self) -> None:
        card = self.card()
        target = next(column for column in self.backend.get_columns() if column.name == DOING.name)

        with patch("pykantui.git.move", return_value=False):
            result = self.backend.move_task(card, target.column_id)

        self.assertFalse(result.ok)
        self.assertEqual(1, len(list(self.workspace.rglob("K-1.md"))))
        self.assertEqual("to-do", self.markdown().parent.name)

    def test_tui_edit_writes_markdown_but_does_not_send_to_provider(self) -> None:
        card = self.card().model_copy(
            update={"title": "Edited in TUI", "description": "TUI body"},
            deep=True,
        )

        result = self.backend.update_task(card)

        self.assertTrue(result.ok, result.message)
        self.assertEqual([], self.provider.updates)
        text = self.markdown().read_text(encoding="utf-8")
        self.assertIn("title: Edited in TUI", text)
        self.assertIn("TUI body", text)
        self.assertEqual(SyncStatus.EDITED.value, self.card().metadata["sync_status"])

    def test_tui_edit_does_not_overwrite_markdown_changed_after_editor_opened(self) -> None:
        card = self.card()
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: External edit"),
            encoding="utf-8",
        )

        result = self.backend.update_task(card.model_copy(update={"title": "Stale TUI edit"}, deep=True))

        self.assertFalse(result.ok)
        self.assertIn("changed", result.message)
        text = path.read_text(encoding="utf-8")
        self.assertIn("title: External edit", text)
        self.assertNotIn("title: Stale TUI edit", text)

    def test_tui_move_does_not_overwrite_markdown_changed_after_card_loaded(self) -> None:
        card = self.card()
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: External edit"),
            encoding="utf-8",
        )
        target = next(column for column in self.backend.get_columns() if column.name == DOING.name)

        result = self.backend.move_task(card, target.column_id)

        self.assertFalse(result.ok)
        self.assertIn("changed", result.message)
        self.assertEqual("to-do", path.parent.name)
        self.assertIn("title: External edit", path.read_text(encoding="utf-8"))

    def test_provider_key_is_shown_in_the_edit_dialog(self) -> None:
        card = self.card()
        screen = TaskDetailScreen(
            task=card,
            column_name="To Do",
            blockers=[],
            blocking=[],
            editing=True,
        )

        self.assertEqual("K-1", screen._key())

    def test_tui_create_writes_a_local_draft_when_provider_supports_create(self) -> None:
        capabilities = self.provider.spec.capabilities.model_copy(update={"create_issues": True})
        self.provider.spec = self.provider.spec.model_copy(  # type: ignore[misc]
            update={"capabilities": capabilities}
        )
        self.backend = ProviderBackend(self.workspace, self.provider, PROJECT)

        result = self.backend.create_task(Task(task_id=99, title="Local draft", column_id=1, description="Draft body"))

        self.assertTrue(result.ok, result.message)
        drafts = [path for path in self.workspace.rglob("*.md") if path.stem.startswith("draft-")]
        self.assertEqual(1, len(drafts))
        self.assertIn("title: Local draft", drafts[0].read_text(encoding="utf-8"))
        created = next(task for task in self.backend.get_tasks() if task.title == "Local draft")
        self.assertEqual(SyncStatus.NEW.value, created.metadata["sync_status"])

    def test_sync_plan_contains_the_local_move_before_any_send(self) -> None:
        card = self.card()
        target = next(column for column in self.backend.get_columns() if column.name == DOING.name)
        self.backend.move_task(card, target.column_id)

        plan = self.backend.plan_sync()

        self.assertEqual([], self.provider.updates)
        self.assertEqual(1, len(plan.pushes))
        self.assertEqual(("column_id",), plan.pushes[0].edit.touched())
        self.assertEqual(DOING.column_id, plan.pushes[0].edit.column_id)

    def test_cancelled_sync_keeps_local_move_and_sends_nothing(self) -> None:
        card = self.card()
        target = next(column for column in self.backend.get_columns() if column.name == DOING.name)
        self.backend.move_task(card, target.column_id)

        report = self.backend.sync_now(confirm=lambda plan: False, commit=False)

        self.assertTrue(report.declined)
        self.assertEqual([], self.provider.updates)
        self.assertEqual("in-progress", next(self.workspace.rglob("K-1.md")).parent.name)

    def test_approved_sync_sends_move_and_returns_card_to_synced(self) -> None:
        card = self.card()
        target = next(column for column in self.backend.get_columns() if column.name == DOING.name)
        self.backend.move_task(card, target.column_id)

        report = self.backend.sync_now(confirm=lambda plan: True, commit=False)

        self.assertEqual(["K-1"], report.pushed)
        self.assertEqual(DOING.column_id, self.provider.updates[-1][1].column_id)
        self.assertEqual(SyncStatus.SYNCED.value, self.card().metadata["sync_status"])

    def test_changed_markdown_after_preview_must_be_confirmed_again(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: First edit"),
            encoding="utf-8",
        )
        plan = self.backend.plan_sync()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: First edit", "title: Different edit"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ProviderError, "changed since the confirmation"):
            self.backend.sync_now(
                confirm=lambda current: True,
                expected_plan=plan,
                commit=False,
            )

        self.assertEqual([], self.provider.updates)

    def test_changed_draft_body_after_preview_must_be_confirmed_again(self) -> None:
        capabilities = self.provider.spec.capabilities.model_copy(update={"create_issues": True})
        self.provider.spec = self.provider.spec.model_copy(  # type: ignore[misc]
            update={"capabilities": capabilities}
        )
        self.backend = ProviderBackend(self.workspace, self.provider, PROJECT)
        self.backend.create_task(Task(task_id=99, title="Draft to review", column_id=1, description="First body"))
        plan = self.backend.plan_sync()
        draft = next(path for path in self.workspace.rglob("*.md") if path.stem.startswith("draft-"))
        draft.write_text(
            draft.read_text(encoding="utf-8").replace("First body", "Different body"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ProviderError, "changed since the confirmation"):
            self.backend.sync_now(
                confirm=lambda current: True,
                expected_plan=plan,
                commit=False,
            )

        self.assertEqual([], self.provider.updates)


if __name__ == "__main__":
    unittest.main()


class SyncTuiJourneyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.provider = RecordingProvider([issue("K-1", TODO)])
        from pykantui.workspace.sync import sync

        sync(self.workspace, self.provider, PROJECT, push_edits=False, commit=False)
        self.backend = ProviderBackend(self.workspace, self.provider, PROJECT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def markdown(self) -> Path:
        return next(self.workspace.rglob("K-1.md"))

    async def test_r_reloads_external_markdown_edit_without_network(self) -> None:
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            path = self.markdown()
            path.write_text(
                path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Reloaded locally"),
                encoding="utf-8",
            )
            self.provider.fetches.clear()

            await pilot.press("r")
            await pilot.pause()

            titles = [task.title for task in self.backend.get_tasks()]
            self.assertTrue(any("Reloaded locally" in title for title in titles))
            self.assertEqual([], self.provider.fetches)

    async def test_sync_key_opens_plan_before_sending(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Ready to send"),
            encoding="utf-8",
        )
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()

            await pilot.press("f5")
            await pilot.pause()

            self.assertIsInstance(app.screen, SyncConfirmScreen)
            text = " ".join(str(label.render()) for label in app.screen.query(Label))
            self.assertIn("K-1", text)
            self.assertIn("Summary", text)
            self.assertEqual([], self.provider.updates)

            await pilot.click("#sync-cancel")
            await pilot.pause()
            self.assertEqual([], self.provider.updates)

    async def test_sync_dialog_names_provider_fields_and_private_notes_separately(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Ready to send"),
            encoding="utf-8",
        )
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()

            text = " ".join(str(label.render()) for label in app.screen.query(Label))
            send_label = str(app.screen.query_one("#sync-send", Button).label)

        self.assertIn("SYNC PREVIEW", text)
        self.assertIn("READY TO SEND (1)", text)
        self.assertIn("UPDATE (1)", text)
        self.assertIn("LOCAL ONLY", text)
        self.assertIn("Private Markdown notes", text)
        self.assertIn("Local Git history", text)
        self.assertEqual("Send ready changes", send_label)
        self.assertNotIn("WILL", text)

    async def test_conflict_dialog_separates_safe_send_from_danger_actions(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Local title"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(update={"title": "Provider title"})
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()

            text = " ".join(str(label.render()) for label in app.screen.query(Label))
            force = app.screen.query_one("#sync-force", Button)

        self.assertIn("BLOCKED (1)", text)
        self.assertNotIn("WILL", text)
        self.assertIn("provider: Provider title", text)
        self.assertIn("local: Local title", text)
        self.assertEqual("error", force.variant)

    async def test_sync_action_rows_stay_inside_the_dialog(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Local title"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(update={"title": "Provider title"})
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()

            dialog = app.screen.query_one("#sync-dialog", Vertical)
            normal = app.screen.query_one("#sync-buttons")
            conflicts = app.screen.query_one("#sync-conflict-buttons")

        self.assertLessEqual(normal.region.bottom, dialog.content_region.bottom)
        self.assertLessEqual(conflicts.region.bottom, dialog.content_region.bottom)

    async def test_conflict_dialog_fits_and_scrolls_at_eighty_by_twenty_four(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace("title: Title K-1", "title: A very long local title that still must remain visible")
            .replace("Body K-1", "A long local description that conflicts with the provider copy"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(
            update={
                "title": "A very long provider title that changed after the last synchronization",
                "body": "A different provider description that must be reviewed safely",
            }
        )
        app = KanbanApp(self.backend)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()

            dialog = app.screen.query_one("#sync-dialog", Vertical)
            send = app.screen.query_one("#sync-send", Button)
            content = app.screen.query_one("#sync-content")

            self.assertLessEqual(dialog.region.right, app.screen.region.right)
            self.assertLessEqual(dialog.region.bottom, app.screen.region.bottom)
            self.assertLessEqual(send.region.bottom, dialog.content_region.bottom)
            self.assertTrue(content.allow_vertical_scroll)
            self.assertGreater(content.max_scroll_y, 0)
            content.focus()
            await pilot.press("pagedown")
            await pilot.pause()
            self.assertGreater(content.scroll_y, 0)

    async def test_open_sync_dialog_reflows_during_live_terminal_resize(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Local title"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(update={"title": "Provider title"})
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            await pilot.resize_terminal(80, 24)
            await pilot.pause()

            dialog = app.screen.query_one("#sync-dialog", Vertical)
            self.assertLessEqual(dialog.region.right, 80)
            self.assertLessEqual(dialog.region.bottom, 24)

            await pilot.resize_terminal(160, 45)
            await pilot.pause()
            self.assertLessEqual(dialog.region.right, 160)
            self.assertLessEqual(dialog.region.bottom, 45)

    async def test_sync_actions_are_compact_rounded_outline_buttons(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Local title"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(update={"title": "Provider title"})
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()

            buttons = list(app.screen.query("#sync-buttons Button, #sync-conflict-buttons Button"))
            borders = {str(button.styles.border.top[0]) for button in buttons}
            separators = [str(label.render()) for label in app.screen.query(".sync-action-separator")]
            background_alphas = {button.styles.background.a for button in buttons}
            danger_border = app.screen.query_one("#sync-force", Button).styles.border.top[1]
            expected_danger = Color.parse(app.current_theme.error)

        self.assertEqual({"round"}, borders)
        self.assertEqual([], separators)
        self.assertEqual({0.0}, background_alphas)
        self.assertLessEqual(
            max(
                abs(danger_border.r - expected_danger.r),
                abs(danger_border.g - expected_danger.g),
                abs(danger_border.b - expected_danger.b),
            ),
            1,
        )

    async def test_use_provider_resolves_conflict_without_losing_private_notes(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace("title: Title K-1", "title: Local title")
            .replace(markdown.NOTES_MARKER, f"{markdown.NOTES_MARKER}\n\nKeep this note"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(update={"title": "Provider title"})
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            await pilot.click("#sync-use-provider")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

        self.assertEqual([], self.provider.updates)
        refreshed = self.markdown().read_text(encoding="utf-8")
        self.assertIn("title: Provider title", refreshed)
        self.assertIn("Keep this note", refreshed)

    async def test_overwrite_provider_sends_the_reviewed_local_conflict(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Local title"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(update={"title": "Provider title"})
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            await pilot.click("#sync-force")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

        self.assertEqual("Local title", self.provider.updates[-1][1].title)
        card = next(task for task in self.backend.get_tasks() if task.metadata["key"] == "K-1")
        self.assertEqual(SyncStatus.SYNCED.value, card.metadata["sync_status"])

    async def test_each_conflicting_field_can_use_a_different_resolution(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace("title: Title K-1", "title: Local title")
            .replace("Body K-1", "Local body"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(
            update={"title": "Provider title", "body": "Provider body"}
        )
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            title_choice = app.screen.query_one("#sync-conflict-0-title", Select)
            body_choice = app.screen.query_one("#sync-conflict-0-body", Select)
            self.assertEqual("Keep undecided", str(title_choice.query_one("#label", Static).content))
            title_choice.value = "provider"
            body_choice.value = "local"

            await pilot.click("#sync-send")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

        self.assertEqual(("body",), self.provider.updates[-1][1].touched())
        refreshed = path.read_text(encoding="utf-8")
        self.assertIn("title: Provider title", refreshed)
        self.assertIn("Local body", refreshed)

    async def test_edit_buttons_fit_inside_the_dialog_at_standard_height(self) -> None:
        app = KanbanApp(self.backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            dialog = app.screen.query_one("#detail-dialog", Vertical)
            buttons = app.screen.query_one("#detail-buttons")
            self.assertLessEqual(buttons.region.bottom, dialog.content_region.bottom)

    async def test_edit_dialog_title_shows_the_provider_key(self) -> None:
        app = KanbanApp(self.backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            dialog = app.screen.query_one("#detail-dialog", Vertical)
            self.assertEqual("K-1", str(dialog.border_title))

    async def test_send_and_sync_applies_the_plan_and_clears_the_marker(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Sent from TUI"),
            encoding="utf-8",
        )
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            await pilot.click("#sync-send")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

            self.assertEqual("Sent from TUI", self.provider.updates[-1][1].title)
            card = next(task for task in self.backend.get_tasks() if task.metadata["key"] == "K-1")
            self.assertEqual(SyncStatus.SYNCED.value, card.metadata["sync_status"])

    async def test_pull_only_keeps_local_edit_and_sends_nothing(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Keep local"),
            encoding="utf-8",
        )
        self.provider._issues.append(issue("K-2", DOING))
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            await pilot.click("#sync-pull")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

            self.assertEqual([], self.provider.updates)
            cards = {str(task.metadata["key"]): task for task in self.backend.get_tasks()}
            self.assertEqual(SyncStatus.EDITED.value, cards["K-1"].metadata["sync_status"])
            self.assertIn("K-2", cards)

    async def test_pull_only_keeps_the_conflict_found_by_the_preview(self) -> None:
        path = self.markdown()
        path.write_text(
            path.read_text(encoding="utf-8").replace("title: Title K-1", "title: Local title"),
            encoding="utf-8",
        )
        self.provider._issues[0] = self.provider._issues[0].model_copy(update={"title": "Provider title"})
        app = KanbanApp(self.backend)
        async with app.run_test(size=(130, 38)) as pilot:
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            await pilot.click("#sync-pull")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

            card = next(task for task in self.backend.get_tasks() if task.metadata["key"] == "K-1")

        self.assertEqual(SyncStatus.CONFLICT.value, card.metadata["sync_status"])


class ProviderAwareEditorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def backend_for(self, name: str) -> tuple[ProviderBackend, RecordingProvider]:
        provider = RecordingProvider([issue("K-1", TODO, body="Provider description", labels=("existing",))])
        provider.spec = get(name).spec  # type: ignore[misc]
        from pykantui.workspace.sync import sync

        sync(self.workspace, provider, PROJECT, push_edits=False, commit=False)
        return ProviderBackend(self.workspace, provider, PROJECT), provider

    async def test_monday_shows_only_its_supported_provider_fields(self) -> None:
        backend, _provider = self.backend_for("monday")
        app = KanbanApp(backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            self.assertFalse(app.screen.query_one("#detail-summary").disabled)
            self.assertFalse(app.screen.query_one("#detail-status").disabled)
            for selector in (
                "#detail-assignee",
                "#detail-issue-type",
                "#detail-priority",
                "#detail-due",
                "#detail-labels",
            ):
                self.assertEqual(0, len(app.screen.query(selector)), selector)
            self.assertEqual(0, len(app.screen.query("#detail-components")))
            description = app.screen.query_one("#detail-notes", TextArea)
            self.assertFalse(description.disabled)
            self.assertTrue(description.read_only)
            self.assertIn(
                "read-only from Monday.com",
                str(description.border_title),
            )
            self.assertFalse(app.screen.query_one("#detail-private-notes").disabled)

    async def test_clickup_enables_all_fields_supported_by_its_task_api(self) -> None:
        backend, _provider = self.backend_for("clickup")
        app = KanbanApp(backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            for selector in (
                "#detail-summary",
                "#detail-status",
                "#detail-notes",
                "#detail-due",
                "#detail-priority",
                "#detail-assignee",
                "#detail-issue-type",
                "#detail-labels",
            ):
                self.assertFalse(app.screen.query_one(selector).disabled, selector)
            self.assertEqual(0, len(app.screen.query("#detail-components")))

    async def test_jira_shows_every_supported_card_field(self) -> None:
        backend, _provider = self.backend_for("jira")
        app = KanbanApp(backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            for selector in (
                "#detail-summary",
                "#detail-assignee",
                "#detail-status",
                "#detail-issue-type",
                "#detail-priority",
                "#detail-due",
                "#detail-labels",
                "#detail-components",
                "#detail-notes",
            ):
                self.assertFalse(app.screen.query_one(selector).disabled, selector)

    async def test_jira_split_editor_keeps_every_supported_field_in_the_sidebar(self) -> None:
        backend, _provider = self.backend_for("jira")
        app = KanbanApp(backend)
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            root_screen = app.screen
            root_stack_size = len(app.screen_stack)

            await pilot.press("e")
            await pilot.pause()

            self.assertIs(app.screen, root_screen)
            self.assertEqual(root_stack_size, len(app.screen_stack))
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))
            for selector in (
                "#work-item-edit-summary",
                "#work-item-edit-assignee",
                "#work-item-edit-status",
                "#work-item-edit-issue-type",
                "#work-item-edit-priority",
                "#work-item-edit-due",
                "#work-item-edit-labels",
                "#work-item-edit-components",
                "#work-item-edit-description",
                "#work-item-edit-private-notes",
            ):
                self.assertFalse(app.query_one(selector).disabled, selector)

    async def test_jira_split_save_writes_markdown_without_contacting_provider(self) -> None:
        backend, provider = self.backend_for("jira")
        app = KanbanApp(backend)
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            root_screen = app.screen
            target_column_id = app.visible_columns[1].column_id

            await pilot.press("e")
            await pilot.pause()
            app.query_one("#work-item-edit-summary", Input).value = "Edited in the split sidebar"
            app.query_one("#work-item-edit-assignee", Input).value = "alex"
            app.query_one("#work-item-edit-status", Select).value = str(target_column_id)
            app.query_one("#work-item-edit-issue-type", Input).value = "Story"
            app.query_one("#work-item-edit-priority", Select).value = "High"
            app.query_one("#work-item-edit-due", Input).value = "2027-04-05"
            app.query_one("#work-item-edit-labels", Input).value = "backend, urgent"
            app.query_one("#work-item-edit-components", Input).value = "API, Platform"
            app.query_one("#work-item-edit-description", TextArea).load_text(
                "Provider description edited in Split."
            )
            app.query_one("#work-item-edit-private-notes", TextArea).load_text(
                "Private note that must remain local."
            )

            await pilot.press("ctrl+s")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

            self.assertIs(app.screen, root_screen)
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))

        parsed = markdown.read(next(self.workspace.rglob("K-1.md")))
        self.assertEqual("Edited in the split sidebar", parsed.front["title"])
        self.assertEqual("alex", parsed.front["assignee"])
        self.assertEqual("Story", parsed.front["type"])
        self.assertEqual("High", parsed.front["priority"])
        self.assertEqual("2027-04-05", parsed.front["due"])
        self.assertEqual(["backend", "urgent"], parsed.front["labels"])
        self.assertEqual(["API", "Platform"], parsed.front["components"])
        self.assertEqual("Provider description edited in Split.", parsed.source)
        self.assertEqual("Private note that must remain local.", parsed.notes)
        self.assertEqual("in-progress", next(self.workspace.rglob("K-1.md")).parent.name)
        self.assertEqual([], provider.updates)
        self.assertEqual([], provider.moves)
        self.assertEqual(
            {
                "title",
                "body",
                "column_id",
                "assignee",
                "issue_type",
                "priority",
                "due_date",
                "labels",
                "components",
            },
            set(backend.plan_sync().pushes[0].edit.touched()),
        )

    async def test_monday_split_editor_omits_unconfigured_provider_fields(self) -> None:
        backend, _provider = self.backend_for("monday")
        app = KanbanApp(backend)
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()

            await pilot.press("e")
            await pilot.pause()

            self.assertFalse(app.query_one("#work-item-edit-summary").disabled)
            self.assertFalse(app.query_one("#work-item-edit-status").disabled)
            description = app.query_one("#work-item-edit-description", TextArea)
            self.assertTrue(description.disabled)
            self.assertIn("read-only from Monday.com", str(description.border_title))
            for selector in (
                "#work-item-edit-assignee",
                "#work-item-edit-issue-type",
                "#work-item-edit-priority",
                "#work-item-edit-due",
                "#work-item-edit-labels",
                "#work-item-edit-components",
            ):
                self.assertEqual(0, len(app.query(selector)), selector)
            self.assertFalse(app.query_one("#work-item-edit-private-notes").disabled)

    async def test_team_context_card_cannot_enter_the_split_editor(self) -> None:
        provider = RecordingProvider([issue("K-1", TODO)])
        provider.spec = get("jira").spec  # type: ignore[misc]
        from pykantui.workspace.sync import sync

        sync(self.workspace, provider, PROJECT, push_edits=False, commit=False)
        provider._issues.append(issue("K-2", TODO, assignee="Another teammate"))
        backend = ProviderBackend(self.workspace, provider, PROJECT, show_team=True)
        app = KanbanApp(backend)
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause()
            app.set_board_layout(BoardLayout.SPLIT)
            await pilot.pause()
            root_screen = app.screen
            root_stack_size = len(app.screen_stack)

            await pilot.press("down")
            await pilot.pause()

            view = app.query_one(WorkItemsView)
            selected = view.selected_task()
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual("K-2", selected.metadata["key"])
            self.assertFalse(selected.metadata["mine"])
            self.assertFalse(backend.can_edit_task(selected))
            self.assertTrue(app.query_one("#work-item-edit-start", Button).disabled)

            await pilot.press("e")
            await pilot.pause()

            self.assertIs(app.screen, root_screen)
            self.assertEqual(root_stack_size, len(app.screen_stack))
            self.assertEqual(0, len(app.screen.query("#detail-dialog")))
            self.assertFalse(view.editing)
            self.assertEqual(0, len(app.query("#work-item-edit-summary")))
            self.assertEqual([], provider.updates)
            self.assertEqual([], provider.moves)

    async def test_provider_destination_and_local_save_wording_are_visible(self) -> None:
        backend, _provider = self.backend_for("clickup")
        app = KanbanApp(backend)
        self.assertIn("ClickUp", app.title)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            description = app.screen.query_one("#detail-notes", TextArea)
            private = app.screen.query_one("#detail-private-notes", TextArea)
            save = app.screen.query_one("#detail-primary", Button)
            self.assertIn("sent to ClickUp", str(description.border_title))
            self.assertIn("local only", str(private.border_title))
            self.assertEqual("Save locally", str(save.label))

    async def test_provider_new_card_is_explicitly_a_local_draft(self) -> None:
        backend, _provider = self.backend_for("jira")
        app = KanbanApp(backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskEditScreen)
            description = app.screen.query_one("#edit-notes", TextArea)
            save = app.screen.query_one("#edit-save", Button)
            self.assertIn("sent to Jira", str(description.border_title))
            self.assertEqual("Save draft locally", str(save.label))

    async def test_jira_new_card_shows_every_creatable_provider_field(self) -> None:
        backend, _provider = self.backend_for("jira")
        app = KanbanApp(backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()

            for selector in (
                "#edit-title",
                "#edit-assignee",
                "#edit-column",
                "#edit-issue-type",
                "#edit-priority",
                "#edit-due",
                "#edit-labels",
                "#edit-components",
                "#edit-notes",
            ):
                self.assertFalse(app.screen.query_one(selector).disabled, selector)

    async def test_jira_component_edits_are_saved_to_local_markdown(self) -> None:
        backend, _provider = self.backend_for("jira")
        app = KanbanApp(backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            app.screen.query_one("#detail-components", Input).value = "API, Platform"

            await pilot.press("ctrl+s")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

        parsed = markdown.read(next(self.workspace.rglob("K-1.md")))
        self.assertEqual(["API", "Platform"], parsed.front["components"])
        self.assertEqual(("components",), backend.plan_sync().pushes[0].edit.touched())

    async def test_private_notes_can_be_saved_from_the_tui_without_provider_work(self) -> None:
        backend, provider = self.backend_for("monday")
        app = KanbanApp(backend)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            app.screen.query_one("#detail-private-notes", TextArea).load_text("Private follow-up for tomorrow.")

            await pilot.press("ctrl+s")
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
            await pilot.pause()

        parsed = markdown.read(next(self.workspace.rglob("K-1.md")))
        self.assertEqual("Private follow-up for tomorrow.", parsed.notes)
        self.assertEqual([], provider.updates)
        self.assertTrue(backend.plan_sync().is_empty())

    def test_backend_discards_fields_monday_cannot_send(self) -> None:
        backend, _provider = self.backend_for("monday")
        card = backend.get_tasks()[0]
        card.title = "Allowed title"
        card.description = "Must not replace the description"
        card.due_date = date(2027, 1, 2)
        card.metadata["labels"] = ["must-not-send"]

        result = backend.update_task(card)

        self.assertTrue(result.ok, result.message)
        parsed = markdown.read(next(self.workspace.rglob("K-1.md")))
        self.assertEqual("Allowed title", parsed.front["title"])
        self.assertEqual("Provider description", parsed.source)
        self.assertEqual(["existing"], parsed.front["labels"])
        self.assertNotIn("due", parsed.front)
        self.assertEqual(("title",), backend.plan_sync().pushes[0].edit.touched())

    def test_backend_rejects_an_empty_provider_title(self) -> None:
        backend, _provider = self.backend_for("monday")
        card = backend.get_tasks()[0]
        card.title = ""

        result = backend.update_task(card)

        self.assertFalse(result.ok)
        self.assertIn("title", result.message)
        parsed = markdown.read(next(self.workspace.rglob("K-1.md")))
        self.assertEqual("Title K-1", parsed.front["title"])

    def test_private_notes_stay_local_without_an_unsent_provider_marker(self) -> None:
        backend, provider = self.backend_for("monday")
        card = backend.get_tasks()[0]
        card.metadata["private_notes"] = "Remember this locally only."

        result = backend.update_task(card)

        self.assertTrue(result.ok, result.message)
        parsed = markdown.read(next(self.workspace.rglob("K-1.md")))
        self.assertEqual("Remember this locally only.", parsed.notes)
        self.assertTrue(backend.plan_sync().is_empty())
        self.assertEqual([], provider.updates)
        refreshed = backend.get_tasks()[0]
        self.assertEqual(SyncStatus.SYNCED.value, refreshed.metadata["sync_status"])
        self.assertEqual("Remember this locally only.", refreshed.metadata["private_notes"])
