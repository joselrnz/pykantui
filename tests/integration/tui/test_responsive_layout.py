"""The TUI must consume every cell after the terminal is resized."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.geometry import Size

from pykantui.config import BoardConfig, ColumnConfig
from pykantui.models import BoardLayout, Task
from pykantui.sync.jsonstore import JsonBackend
from pykantui.tui.app import KanbanApp
from pykantui.tui.terminal import current_terminal_size
from pykantui.tui.widgets.work_items import WorkItemsView


class ResponsiveLayoutTests(unittest.IsolatedAsyncioTestCase):
    def test_terminal_size_uses_the_same_input_tty_as_textual(self) -> None:
        expected = os.terminal_size((160, 44))
        with patch("pykantui.tui.terminal.os.get_terminal_size", return_value=expected) as get_size:
            self.assertEqual(Size(160, 44), current_terminal_size())

        stdin = sys.__stdin__
        if stdin is None:
            self.fail("Python did not expose its original standard-input stream")
        get_size.assert_called_once_with(stdin.fileno())

    def test_terminal_size_is_optional_without_a_controlling_terminal(self) -> None:
        with patch("pykantui.tui.terminal.os.get_terminal_size", side_effect=OSError):
            self.assertIsNone(current_terminal_size())

    async def test_missed_pty_resize_is_recovered_by_terminal_polling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("PYKANTUI_HOME")
            os.environ["PYKANTUI_HOME"] = directory
            try:
                app = KanbanApp(_backend(Path(directory)), confirm_moves=False)
                async with app.run_test(size=(100, 28)) as pilot:
                    app.set_board_layout(BoardLayout.SPLIT)
                    with patch("pykantui.tui.terminal.current_terminal_size", return_value=Size(170, 46)):
                        app._poll_terminal_size()
                    await pilot.pause()

                    view = app.query_one(WorkItemsView)
                    self.assertEqual((170, 43), (view.region.width, view.region.height))
            finally:
                if previous is None:
                    os.environ.pop("PYKANTUI_HOME", None)
                else:
                    os.environ["PYKANTUI_HOME"] = previous

    async def test_split_view_tracks_terminal_growth_and_shrinkage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("PYKANTUI_HOME")
            os.environ["PYKANTUI_HOME"] = directory
            try:
                app = KanbanApp(_backend(Path(directory)), confirm_moves=False)
                async with app.run_test(size=(100, 28)) as pilot:
                    app.set_board_layout(BoardLayout.SPLIT)
                    await pilot.pause()
                    view = app.query_one(WorkItemsView)
                    self.assertEqual((100, 25), (view.region.width, view.region.height))

                    await pilot.resize_terminal(170, 46)
                    await pilot.pause()
                    self.assertEqual((170, 43), (view.region.width, view.region.height))

                    await pilot.resize_terminal(82, 24)
                    await pilot.pause()
                    self.assertEqual((82, 21), (view.region.width, view.region.height))
            finally:
                if previous is None:
                    os.environ.pop("PYKANTUI_HOME", None)
                else:
                    os.environ["PYKANTUI_HOME"] = previous


def _backend(path: Path) -> JsonBackend:
    config = BoardConfig(
        columns=[
            ColumnConfig(column_id=1, name="To Do", position=0),
            ColumnConfig(column_id=2, name="Done", position=1),
        ],
        reset_column=1,
        start_column=1,
        finish_column=2,
    )
    backend = JsonBackend(path=path / "board.json", config=config)
    backend.create_task(Task(task_id=1, title="Resize the board", column_id=1))
    return backend


if __name__ == "__main__":
    unittest.main()
