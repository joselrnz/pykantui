"""Architecture gates for the Textual application shell."""

from __future__ import annotations

import unittest
from pathlib import Path

from pykantui.tui.app import KanbanApp
from pykantui.tui.controllers import (
    CardController,
    ColumnController,
    MenuController,
    ProjectController,
    SyncController,
    ViewActionController,
)


class TuiArchitectureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = Path(__file__).parents[3] / "src" / "pykantui" / "tui"

    def test_application_shell_stays_below_five_hundred_lines(self) -> None:
        lines = (self.package / "app.py").read_text(encoding="utf-8").splitlines()

        self.assertLess(len(lines), 500)

    def test_work_item_widgets_stay_in_focused_modules(self) -> None:
        widgets = self.package / "widgets"

        for module in (
            "work_items.py",
            "work_item_table.py",
            "work_item_editors.py",
            "work_item_detail.py",
            "work_item_compose.py",
            "work_item_resize.py",
        ):
            with self.subTest(module=module):
                lines = (widgets / module).read_text(encoding="utf-8").splitlines()
                self.assertLess(len(lines), 500)

    def test_application_uses_focused_controllers(self) -> None:
        for controller in (
            CardController,
            ColumnController,
            MenuController,
            ProjectController,
            SyncController,
            ViewActionController,
        ):
            with self.subTest(controller=controller.__name__):
                self.assertTrue(issubclass(KanbanApp, controller))

    def test_each_controller_owns_one_tui_responsibility(self) -> None:
        controllers = self.package / "controllers"

        self.assertEqual(
            {"__init__.py", "actions.py", "cards.py", "columns.py", "menu.py", "projects.py", "sync.py"},
            {path.name for path in controllers.glob("*.py")},
        )

    def test_controllers_do_not_reach_into_provider_packages(self) -> None:
        controllers = self.package / "controllers"

        for source in controllers.glob("*.py"):
            with self.subTest(controller=source.name):
                self.assertNotIn("pykantui.providers", source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
