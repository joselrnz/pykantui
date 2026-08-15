"""Algorithmic performance contracts for board-column reconciliation."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from pykantui.models import Task
from pykantui.tui.widgets.column import BoardColumn


class _Card:
    def __init__(self, task_id: int) -> None:
        self.task_ = Task(task_id=task_id, title=str(task_id), column_id=1)
        self.remove = AsyncMock()


class BoardColumnPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_removal_sync_builds_the_wanted_id_set_once(self) -> None:
        """Removing one card must not allocate the same large set per card."""
        column = BoardColumn("To Do", 1)
        current = [_Card(task_id) for task_id in range(500)]
        remaining = current[:-1]
        wanted = [card.task_.model_copy(deep=True) for card in remaining]
        refresh = Mock()

        with (
            patch.object(column, "cards", side_effect=[current, []]) as cards,
            patch.object(column, "_refresh_in_place", new=refresh),
            patch("pykantui.tui.widgets.column.set", wraps=set, create=True) as make_set,
        ):
            await column.sync(wanted)

        self.assertEqual(1, make_set.call_count)
        self.assertEqual(1, cards.call_count)
        refresh.assert_called_once_with(remaining, wanted)
        current[-1].remove.assert_awaited_once()
