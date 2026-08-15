"""Jira's expanded JQL control performs one read-only provider pull."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

from rich.cells import cell_len
from textual.widgets import Button, Input

from pykantui.commands.new import write_draft
from pykantui.sync.jsonstore import JsonBackend
from pykantui.sync.provider import ProviderBackend
from pykantui.tracker import get
from pykantui.tracker.models import IssueDraft, RemoteIssue
from pykantui.tui.app import KanbanApp
from pykantui.tui.glyphs import SEARCH_GLYPH
from pykantui.workspace.project import Project
from pykantui.workspace.sync import sync
from tests.integration.sync.test_push import DOING, PROJECT, TODO, RecordingProvider, issue


class JiraQueryProvider(RecordingProvider):
    """Jira-shaped provider that records issue-list reads without HTTP."""

    spec = get("jira").spec

    def __init__(self) -> None:
        super().__init__([issue("JPT-1", TODO), issue("JPT-2", DOING)])
        self.config = {"jql": ""}
        self.query_reads: list[str] = []

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        query = str(self.config.get("jql", "") or "")
        self.query_reads.append(query)
        if query == "invalid jql":
            raise RuntimeError("invalid Jira query")
        issues = list(super().iter_issues(project_id))
        if "In Progress" in query:
            issues = [item for item in issues if item.status == DOING.name]
        return iter(issues)


class JiraQueryJourneyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.provider = JiraQueryProvider()
        sync(
            self.workspace,
            self.provider,
            PROJECT,
            push_edits=False,
            commit=False,
        )
        project = Project(
            provider="jira",
            project_id=PROJECT.project_id,
            key=PROJECT.key,
            name=PROJECT.name,
            config={"jql": ""},
        )
        project.save(self.workspace)
        write_draft(
            self.workspace,
            project,
            TODO,
            IssueDraft(title="Local draft survives JQL"),
        )
        self.provider.query_reads.clear()
        self.backend = ProviderBackend(self.workspace, self.provider, PROJECT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def hashes(self) -> dict[str, str]:
        return {
            str(path.relative_to(self.workspace)): sha256(path.read_bytes()).hexdigest()
            for path in self.workspace.rglob("*")
            if path.is_file()
        }

    def visible_keys(self) -> list[str]:
        return [str(task.metadata.get("key", "") or task.title) for task in self.backend.get_tasks()]

    def test_query_capability_is_derived_from_the_provider_instance(self) -> None:
        asana = RecordingProvider([issue("A-1", TODO)])
        asana.spec = get("asana").spec  # type: ignore[misc]
        asana_workspace = self.workspace / "asana"
        sync(asana_workspace, asana, PROJECT, push_edits=False, commit=False)

        self.assertTrue(self.backend.can_run_query())
        self.assertFalse(ProviderBackend(asana_workspace, asana, PROJECT).can_run_query())

    async def test_expanded_query_search_is_width_safe_and_theme_accented(self) -> None:
        app = KanbanApp(self.backend, confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()
            query = app.menu_bar.query_one("#filter-query", Input)
            search = app.menu_bar.query_one("#filter-search", Button)

            self.assertFalse(query.disabled)
            self.assertFalse(search.disabled)
            self.assertEqual("⌕", SEARCH_GLYPH)
            self.assertEqual(1, cell_len(SEARCH_GLYPH))
            self.assertEqual(f"{SEARCH_GLYPH} Search", str(search.label))
            self.assertLessEqual(search.region.right, app.menu_bar.content_region.right)
            for theme in sorted(app.available_themes):
                with self.subTest(theme=theme):
                    app.theme = theme
                    await pilot.pause()
                    self.assertEqual(
                        app.theme_variables["text-accent"],
                        search.styles.color.hex,
                    )
                    self.assertEqual(
                        app.theme_variables["accent"],
                        search.styles.border.top[1].hex,
                    )
                    self.assertEqual("round", search.styles.border.top[0])
                    self.assertEqual(0.0, search.styles.background.a)

    async def test_search_runs_one_read_only_jira_query(self) -> None:
        before = self.hashes()
        self.provider._issues.append(issue("JPT-3", DOING))
        app = KanbanApp(self.backend, confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()
            query = app.menu_bar.query_one("#filter-query", Input)
            search = app.menu_bar.query_one("#filter-search", Button)

            self.assertFalse(query.disabled)
            self.assertFalse(search.disabled)
            query.value = 'status = "In Progress"'
            await pilot.click("#filter-search")
            await app.workers.wait_for_complete()
            await pilot.pause()

        self.assertEqual(['status = "In Progress"'], self.provider.query_reads)
        self.assertEqual('status = "In Progress"', self.backend.query_text())
        self.assertEqual("", self.provider.config.get("jql"))
        self.assertEqual(
            {"JPT-2", "draft-local-draft-survives-jql", "JPT-3"},
            set(self.visible_keys()),
        )
        remote_only = next(task for task in self.backend.get_tasks() if task.metadata.get("key") == "JPT-3")
        local_match = next(task for task in self.backend.get_tasks() if task.metadata.get("key") == "JPT-2")
        self.assertFalse(self.backend.can_edit_task(remote_only))
        self.assertTrue(self.backend.can_edit_task(local_match))
        self.assertEqual(before, self.hashes())
        self.assertEqual([], self.provider.fetches, "JQL search must not probe every issue")
        self.assertEqual([], self.provider.updates)
        self.assertEqual([], self.provider.moves)

    async def test_enter_in_jql_runs_the_same_single_read_path(self) -> None:
        app = KanbanApp(self.backend, confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()
            query = app.menu_bar.query_one("#filter-query", Input)
            query.value = 'status = "In Progress"'
            query.focus()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

        self.assertEqual(['status = "In Progress"'], self.provider.query_reads)
        self.assertEqual([], self.provider.fetches)
        self.assertEqual([], self.provider.updates)
        self.assertEqual([], self.provider.moves)

    async def test_empty_jql_clears_the_extra_clause_without_an_api_read(self) -> None:
        self.provider.config["jql"] = "labels = backend"
        app = KanbanApp(self.backend, confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()
            query = app.menu_bar.query_one("#filter-query", Input)
            query.value = ""
            await pilot.click("#filter-search")
            await app.workers.wait_for_complete()
            await pilot.pause()

        self.assertEqual([], self.provider.query_reads)
        self.assertEqual("", self.backend.query_text())
        self.assertEqual("labels = backend", self.provider.config.get("jql"))
        self.assertEqual(
            {"JPT-1", "JPT-2", "draft-local-draft-survives-jql"},
            set(self.visible_keys()),
        )
        self.assertEqual([], self.provider.updates)
        self.assertEqual([], self.provider.moves)

    async def test_local_json_board_has_no_remote_query_controls(self) -> None:
        app = KanbanApp(JsonBackend(), confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()

            self.assertEqual(0, len(app.menu_bar.query("#filter-query")))
            self.assertEqual(0, len(app.menu_bar.query("#filter-search")))

    async def test_failed_jql_does_not_replace_the_last_working_query(self) -> None:
        self.backend.run_query('status = "In Progress"')
        self.provider.query_reads.clear()
        before_hashes = self.hashes()
        before_keys = self.visible_keys()
        app = KanbanApp(self.backend, confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()
            app.menu_bar.query_one("#filter-query", Input).value = "invalid jql"
            await pilot.click("#filter-search")
            await app.workers.wait_for_complete()
            await pilot.pause()

        self.assertEqual('status = "In Progress"', self.backend.query_text())
        self.assertEqual("", self.provider.config.get("jql"))
        self.assertEqual(["invalid jql"], self.provider.query_reads)
        self.assertEqual(before_keys, self.visible_keys())
        self.assertEqual(before_hashes, self.hashes())
        self.assertEqual([], self.provider.fetches)
        self.assertEqual([], self.provider.updates)
        self.assertEqual([], self.provider.moves)

    async def test_clearing_jql_restores_every_local_card_without_an_api_read(self) -> None:
        self.provider._issues.append(issue("JPT-3", DOING))
        app = KanbanApp(self.backend, confirm_moves=False)

        async with app.run_test(size=(160, 42)) as pilot:
            await pilot.pause()
            await pilot.press("f2", "f2")
            await pilot.pause()
            query = app.menu_bar.query_one("#filter-query", Input)
            query.value = 'status = "In Progress"'
            await pilot.click("#filter-search")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(
                {"JPT-2", "draft-local-draft-survives-jql", "JPT-3"},
                set(self.visible_keys()),
            )

            query.value = ""
            await pilot.click("#filter-search")
            await app.workers.wait_for_complete()
            await pilot.pause()

        self.assertEqual(
            {"JPT-1", "JPT-2", "draft-local-draft-survives-jql"},
            set(self.visible_keys()),
        )
        self.assertEqual(['status = "In Progress"'], self.provider.query_reads)
        self.assertEqual([], self.provider.fetches)
