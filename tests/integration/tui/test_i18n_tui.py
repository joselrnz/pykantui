"""Primary Textual shell controls honor the active gettext locale."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from textual.widgets import Button, Label

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.core.actions import Menu
from pykantui.core.filters import BoardView
from pykantui.i18n import Locale, using_locale
from pykantui.models import BoardLayout, MovementMode, Task
from pykantui.pages.confirm import ConfirmMoveScreen
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tracker.models import IssueEdit, RemoteIssue
from pykantui.tui.app import KanbanApp
from pykantui.tui.menu_items import build_menu_items
from pykantui.tui.widgets.card import TaskCard
from pykantui.workspace.models import PendingPush, SyncPlan
from pykantui.workspace.status import SyncStatus, summarise


def localized_app() -> KanbanApp:
    config = BoardConfig(columns=[ColumnConfig(column_id=1, name="Backlog", position=0)])
    return KanbanApp(JsonBackend(config=config))


class LocalizedShellTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_and_toolbar_are_spanish(self) -> None:
        with using_locale(Locale.SPANISH):
            app = localized_app()
            async with app.run_test(size=(120, 30)):
                menu = str(app.query_one("#app-header-menu", Label).content)
                home = str(app.query_one("#bar-home", Label).content)

        self.assertEqual("⌘ Menú", menu)
        self.assertEqual("⌂ Inicio", home)

    async def test_move_confirmation_is_spanish_but_card_content_is_untouched(self) -> None:
        with using_locale(Locale.SPANISH):
            app = localized_app()
            async with app.run_test(size=(120, 30)) as pilot:
                await app.push_screen(ConfirmMoveScreen("Fix OAuth callback", "Backlog", "Done"))
                await pilot.pause()
                heading = str(app.screen.query_one("#confirm-heading", Label).content)
                card = str(app.screen.query_one("#confirm-card", Label).content)
                cancel = str(app.screen.query_one("#confirm-cancel", Button).label)

        self.assertEqual("¿Mover esta tarjeta?", heading)
        self.assertEqual("Fix OAuth callback", card)
        self.assertEqual("Cancelar", cancel)

    async def test_header_and_toolbar_are_french(self) -> None:
        with using_locale(Locale.FRENCH):
            app = localized_app()
            async with app.run_test(size=(120, 30)):
                menu = str(app.query_one("#app-header-menu", Label).content)
                home = str(app.query_one("#bar-home", Label).content)
                summary = app.view_summary()

        self.assertEqual("⌘ Menu", menu)
        self.assertEqual("⌂ Accueil", home)
        self.assertEqual("0 carte", summary)

    async def test_header_and_toolbar_are_german(self) -> None:
        with using_locale(Locale.GERMAN):
            app = localized_app()
            async with app.run_test(size=(120, 30)):
                menu = str(app.query_one("#app-header-menu", Label).content)
                home = str(app.query_one("#bar-home", Label).content)
                summary = app.view_summary()

        self.assertEqual("⌘ Menü", menu)
        self.assertEqual("⌂ Start", home)
        self.assertEqual("0 Karten", summary)

    async def test_header_and_toolbar_are_simplified_chinese(self) -> None:
        with using_locale(Locale.SIMPLIFIED_CHINESE):
            app = localized_app()
            async with app.run_test(size=(120, 30)):
                menu = str(app.query_one("#app-header-menu", Label).content)
                home = str(app.query_one("#bar-home", Label).content)
                summary = app.view_summary()

        self.assertEqual("⌘ 菜单", menu)
        self.assertEqual("⌂ 首页", home)
        self.assertEqual("0 卡片", summary)

    async def test_header_and_toolbar_are_traditional_chinese(self) -> None:
        with using_locale(Locale.TRADITIONAL_CHINESE):
            app = localized_app()
            async with app.run_test(size=(120, 30)):
                menu = str(app.query_one("#app-header-menu", Label).content)
                home = str(app.query_one("#bar-home", Label).content)
                summary = app.view_summary()

        self.assertEqual("⌘ 選單", menu)
        self.assertEqual("⌂ 首頁", home)
        self.assertEqual("0 張卡片", summary)

    async def test_header_and_toolbar_are_arabic(self) -> None:
        with using_locale(Locale.ARABIC):
            app = localized_app()
            async with app.run_test(size=(120, 30)):
                menu = str(app.query_one("#app-header-menu", Label).content)
                home = str(app.query_one("#bar-home", Label).content)
                summary = app.view_summary()

        self.assertEqual("⌘ القائمة", menu)
        self.assertEqual("⌂ الرئيسية", home)
        self.assertEqual("0 بطاقة", summary)


class LocalizedCardMetadataTests(unittest.TestCase):
    def test_chinese_sync_plan_translates_headings_fields_and_conflict_values(self) -> None:
        before = RemoteIssue(issue_id="1", key="JPT-1", title="Old", column_id="todo")
        remote = before.model_copy(update={"title": "Provider title"})
        plan = SyncPlan(
            pushes=[
                PendingPush(
                    key="JPT-1",
                    previous=before,
                    remote=remote,
                    edit=IssueEdit(title="Local title"),
                    conflict=True,
                )
            ],
            creates=["New story"],
            create_details=["signature"],
        )

        with using_locale(Locale.SIMPLIFIED_CHINESE):
            rendered = plan.describe()

        self.assertIn("准备发送 (1)", rendered)
        self.assertIn("创建 (1)", rendered)
        self.assertIn("已阻止 (1)", rendered)
        self.assertIn("提供商: Provider title", rendered)
        self.assertIn("本地: Local title", rendered)
        self.assertNotRegex(rendered, r"READY TO SEND|CREATE|BLOCKED|provider:|local:")

    def test_french_sync_states_and_summary_are_localized(self) -> None:
        with using_locale(Locale.FRENCH):
            labels = [status.label for status in SyncStatus]
            summary = summarise([SyncStatus.EDITED, SyncStatus.CONFLICT])

        self.assertEqual(
            ["synchro", "à envoyer", "conflit", "hors synchro", "Markdown invalide"],
            labels,
        )
        self.assertEqual("1 à envoyer, 1 conflit", summary)

    def test_french_card_metadata_is_localized(self) -> None:
        task = Task(
            task_id=1,
            title="Provider title stays intact",
            column_id=1,
            created_at=datetime.now() - timedelta(days=2),
            due_date=date.today() + timedelta(days=3),
            metadata={"sync_status": SyncStatus.EDITED.value},
        )

        with using_locale(Locale.FRENCH):
            metadata = TaskCard(task, row=0).metadata_line()

        self.assertIn("à envoyer", metadata)
        self.assertIn("âge 2j", metadata)
        self.assertIn("dû +3j", metadata)
        self.assertNotIn("unsent edit", metadata)


class MultiscriptContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_titles_survive_latin_cyrillic_cjk_devanagari_and_rtl_scripts(self) -> None:
        titles = (
            "Überprüfung der Anmeldung",
            "Проверить синхронизацию",
            "支払い同期を確認",
            "결제 동기화 확인",
            "भुगतान सिंक जाँचें",
            "مراجعة مزامنة الدفع",
            "בדיקת סנכרון תשלום",
        )
        config = BoardConfig(columns=[ColumnConfig(column_id=1, name="Backlog", position=0)])
        backend = JsonBackend(config=config)
        for task_id, title in enumerate(titles, start=1):
            backend.create_task(Task(task_id=task_id, title=title, column_id=1))

        with using_locale(Locale.FRENCH):
            app = KanbanApp(backend)
            async with app.run_test(size=(120, 45)):
                rendered = {str(label.content) for label in app.query(".card-title").results(Label)}

        self.assertEqual(set(titles), rendered)


class LocalizedMenuTests(unittest.TestCase):
    def test_main_menu_translates_commands_but_not_provider_names(self) -> None:
        with using_locale(Locale.SPANISH):
            items = build_menu_items(
                Menu.MAIN,
                view=BoardView(),
                board_layout=BoardLayout.KANBAN,
                movement_mode=MovementMode.ADJACENT,
                confirm_moves=True,
                supports_sync=True,
                provider_fields=(),
                saved_filters={},
                filter_prefix="filter-",
            )

        labels = [item.label for item in items]
        self.assertIn("Nueva tarjeta", labels)
        self.assertIn("Sincronizar con el proveedor…", labels)
        self.assertIn("Ayuda", labels)


if __name__ == "__main__":
    unittest.main()
