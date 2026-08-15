"""The workspace graph: what it reads, and what it refuses to invent."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pykantui.tracker.models import RemoteColumn, RemoteProject
from pykantui.workspace import graph as graph_module
from pykantui.workspace import layout
from pykantui.workspace.status import SyncStatus

TODO = RemoteColumn(column_id="1", name="To Do", group="todo")
DOING = RemoteColumn(column_id="2", name="In Progress", group="started")
DONE = RemoteColumn(column_id="3", name="Done", group="done")
COLUMNS = [TODO, DOING, DONE]
PROJECT = RemoteProject(project_id="P1", key="JPT")


class GraphCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        layout.meta_dir(self.ws).mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def card(self, key: str, column: str, *, parent: str = "", title: str = "") -> None:
        folder = self.ws / "jira" / "projects" / "JPT" / column
        folder.mkdir(parents=True, exist_ok=True)
        front = [f"key: {key}", f'id: "{key}"', f"title: {title or key}", f"column: {column}"]
        if parent:
            front.append(f"parent: {parent}")
        (folder / f"{key}.md").write_text(
            "---\n" + "\n".join(front) + "\n---\n\n<!-- pykantui:source -->\n\n<!-- pykantui:notes -->\n",
            encoding="utf-8",
        )

    def build(self) -> graph_module.Graph:
        return graph_module.read(self.ws, "jira", PROJECT, COLUMNS)


class ReadTests(GraphCase):
    def test_it_finds_every_card(self) -> None:
        self.card("JPT-1", "to-do")
        self.card("JPT-2", "in-progress")
        self.assertEqual({"JPT-1", "JPT-2"}, {n.key for n in self.build().nodes})

    def test_columns_become_lanes_in_tracker_order(self) -> None:
        self.card("JPT-1", "to-do")
        self.card("JPT-2", "done")
        lanes = {n.key: n.lane for n in self.build().nodes}
        self.assertEqual(0, lanes["JPT-1"])
        self.assertEqual(2, lanes["JPT-2"])

    def test_a_parent_link_becomes_an_edge(self) -> None:
        self.card("JPT-1", "to-do")
        self.card("JPT-2", "in-progress", parent="JPT-1")
        edges = self.build().edges()
        self.assertEqual(1, len(edges))
        self.assertEqual(("JPT-1", "JPT-2"), (edges[0][0].key, edges[0][1].key))

    def test_a_parent_outside_the_workspace_is_dropped_not_invented(self) -> None:
        """Drawing a node for something never read would be drawing a guess."""
        self.card("JPT-2", "in-progress", parent="OTHER-9")
        picture = self.build()
        self.assertEqual([], picture.edges())
        self.assertEqual(["JPT-2"], [n.key for n in picture.orphans()])

    def test_cards_in_one_column_stack_rather_than_overlap(self) -> None:
        for key in ("JPT-1", "JPT-2", "JPT-3"):
            self.card(key, "in-progress")
        rows = sorted(n.row for n in self.build().nodes)
        self.assertEqual([0, 1, 2], rows)

    def test_an_empty_workspace_is_an_empty_graph_not_a_crash(self) -> None:
        picture = self.build()
        self.assertEqual([], picture.nodes)
        self.assertEqual([], picture.edges())


class RenderTests(GraphCase):
    def html(self) -> str:
        return graph_module.render(self.build())

    def test_it_is_self_contained(self) -> None:
        """No CDN, no external anything -- the file must still work offline."""
        self.card("JPT-1", "to-do")
        page = self.html()
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("<script src", page)

    def test_both_themes_are_defined(self) -> None:
        self.card("JPT-1", "to-do")
        page = self.html()
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn('data-theme="dark"', page)
        self.assertIn('data-theme="light"', page)

    def test_a_title_with_markup_is_escaped(self) -> None:
        """A title is data; it must never become structure."""
        self.card("JPT-1", "to-do", title="<script>alert(1)</script>")
        page = self.html()
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_an_empty_board_says_so_rather_than_drawing_nothing(self) -> None:
        page = self.html()
        self.assertIn("kbn sync", page)

    def test_the_counts_are_reported(self) -> None:
        self.card("JPT-1", "to-do")
        self.card("JPT-2", "in-progress", parent="JPT-1")
        page = self.html()
        self.assertIn("2 issues", page)
        self.assertIn("1 parent links", page)

    def test_state_colour_matches_the_board(self) -> None:
        """The graph and the TUI must not invent separate vocabularies."""
        for status in SyncStatus:
            self.assertIn(status, graph_module._COLOURS)

    def test_the_output_is_stable(self) -> None:
        """Same workspace, same file -- so committing it produces no churn."""
        from datetime import datetime

        self.card("JPT-1", "to-do")
        when = datetime(2026, 1, 1, 12, 0)
        first = graph_module.render(self.build(), generated=when)
        second = graph_module.render(self.build(), generated=when)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
