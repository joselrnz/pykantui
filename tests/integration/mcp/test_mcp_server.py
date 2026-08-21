"""The MCP tool surface, end to end against a real (FakeProvider) workspace.

No stdio transport is spun up here -- ``@mcp.tool()`` in the installed SDK
returns the original function unchanged, so every tool in ``server.py`` is
called directly, the same way any other Python function is tested. Only the
transport layer (untested here) is FastMCP's; every behavior below is ours.
"""

from __future__ import annotations

import inspect
import io
import itertools
import os
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pykantui.cli.main import main
from pykantui.mcp import cards, server
from pykantui.tracker import register, unregister
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import IssueDraft, IssueEdit, RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, FieldKind, ProviderField, ProviderSpec
from pykantui.workspace import sync as workspace_sync

TODO = RemoteColumn(column_id="1", name="To Do", group="todo")
DOING = RemoteColumn(column_id="2", name="Doing", group="started")
DONE = RemoteColumn(column_id="3", name="Done", group="done")
PROJECT = RemoteProject(project_id="P1", key="DEMO", name="Demo project")


class FakeProvider(Provider):
    """A tracker that can create issues, so a full sync round-trip is real."""

    spec = ProviderSpec(
        name="faketracker",
        label="FakeTracker",
        token_url="https://example.com/tokens",
        auth_fields=(
            ProviderField(name="token", label="API token", kind=FieldKind.SECRET, env_vars=("FAKETRACKER_TOKEN",)),
        ),
        config_fields=(ProviderField(name="project_id", label="Project", kind=FieldKind.CHOICE),),
        capabilities=Capabilities(
            move_issues=True, create_issues=True, writable_fields=("title", "body", "column_id", "labels")
        ),
    )
    projects: list[RemoteProject] = [PROJECT]
    _ids = itertools.count(100)
    _created: dict[str, RemoteIssue] = {}

    def verify(self) -> RemoteUser:
        return RemoteUser(display_name="tester", email="t@example.com")

    def list_projects(self) -> list[RemoteProject]:
        return list(self.projects)

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        return [TODO, DOING, DONE]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        return iter(list(FakeProvider._created.values()))

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        number = next(FakeProvider._ids)
        issue = RemoteIssue(
            issue_id=str(number),
            key=f"DEMO-{number}",
            title=draft.title,
            body=draft.body,
            column_id=draft.column_id,
            status="To Do" if draft.column_id == TODO.column_id else "Done",
            labels=draft.labels,
        )
        FakeProvider._created[issue.issue_id] = issue
        return issue


def run_cli(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(list(argv))
    return code, buffer.getvalue()


class McpCase(unittest.TestCase):
    """One FakeProvider-backed workspace, git-initialized, per test."""

    def setUp(self) -> None:
        register("faketracker", lambda: FakeProvider, replace=True)
        FakeProvider._created = {}
        self._tmp = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"PYKANTUI_HOME": self._home.name}, clear=False)
        self._env.start()
        self.workspace = Path(self._tmp.name) / "board"
        code, out = run_cli(
            "init", "--type", "faketracker", "--path", str(self.workspace),
            "--token", "t", "--project-id", "P1", "--yes",
        )
        assert code == 0, out
        self.ws = str(self.workspace)

    def tearDown(self) -> None:
        self._env.stop()
        self._home.cleanup()
        self._tmp.cleanup()
        unregister("faketracker")


class CreateCardTests(McpCase):
    def test_a_minimal_card_drafts_with_no_dependency_or_assignment(self) -> None:
        card = cards.create_card(self.ws, "Set up the project skeleton")
        self.assertEqual("Set up the project skeleton", card["title"])
        self.assertEqual([], card["blocked_by"])
        self.assertEqual("", card["assigned_agent"])
        self.assertTrue(card["committed"])

    def test_dependency_labels_and_assignment_all_land_together(self) -> None:
        parent = cards.create_card(self.ws, "Backend API")
        child = cards.create_card(
            self.ws, "Frontend UI",
            blocked_by=[parent["id"]], assigned_agent="codex", labels=["frontend"],
        )
        self.assertEqual([parent["id"]], child["blocked_by"])
        self.assertEqual("codex", child["assigned_agent"])
        self.assertEqual(["frontend"], child["labels"])

    def test_same_title_same_column_gets_distinct_ids(self) -> None:
        """write_draft's own collision-retry -- exercised under the MCP lock."""
        first = cards.create_card(self.ws, "Duplicate title")
        second = cards.create_card(self.ws, "Duplicate title")
        self.assertNotEqual(first["id"], second["id"])

    def test_no_git_workspace_skips_the_commit_without_erroring(self) -> None:
        no_git_ws = Path(self._tmp.name) / "board-no-git"
        code, out = run_cli(
            "init", "--type", "faketracker", "--path", str(no_git_ws),
            "--token", "t", "--project-id", "P1", "--yes", "--no-git",
        )
        assert code == 0, out
        card = cards.create_card(str(no_git_ws), "A card with no git history")
        self.assertFalse(card["committed"])
        self.assertEqual("", card["warning"])

    def test_a_git_backed_create_produces_a_labeled_commit(self) -> None:
        card = cards.create_card(self.ws, "A card with real git history", agent_name="codex")
        self.assertTrue(card["committed"])
        log = subprocess.run(
            ["git", "-C", str(self.workspace), "log", "--oneline", "-1"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn(f"mcp(codex): create {card['id']}", log)


class DependencyGateTests(McpCase):
    def test_a_card_blocked_by_an_unfinished_card_cannot_move(self) -> None:
        blocker = cards.create_card(self.ws, "Backend API")
        blocked = cards.create_card(self.ws, "Frontend UI", blocked_by=[blocker["id"]])
        with self.assertRaises(ProviderError) as raised:
            cards.move_card(self.ws, blocked["id"], "Doing")
        self.assertIn("blocked by", str(raised.exception))
        self.assertIn("Backend API", str(raised.exception))

    def test_finishing_the_blocker_unblocks_the_move(self) -> None:
        blocker = cards.create_card(self.ws, "Backend API")
        blocked = cards.create_card(self.ws, "Frontend UI", blocked_by=[blocker["id"]])
        cards.move_card(self.ws, blocker["id"], "Done")
        moved = cards.move_card(self.ws, blocked["id"], "Doing")
        self.assertEqual("doing", moved["column"])

    def test_set_dependency_clears_blockers_without_touching_assignment(self) -> None:
        blocker = cards.create_card(self.ws, "Backend API")
        blocked = cards.create_card(self.ws, "Frontend UI", blocked_by=[blocker["id"]], assigned_agent="codex")
        cleared = cards.set_dependency(self.ws, blocked["id"], [])
        self.assertEqual([], cleared["blocked_by"])
        self.assertEqual("codex", cleared["assigned_agent"])

    def test_an_unresolvable_blocker_id_does_not_block_forever(self) -> None:
        """Fail open: a typo or a key from elsewhere must never wedge a card."""
        card = cards.create_card(self.ws, "Solo card", blocked_by=["no-such-card"])
        moved = cards.move_card(self.ws, card["id"], "Doing")
        self.assertEqual("doing", moved["column"])


class AssignAgentTests(McpCase):
    def test_assign_agent_does_not_disturb_blocked_by(self) -> None:
        blocker = cards.create_card(self.ws, "Backend API")
        card = cards.create_card(self.ws, "Frontend UI", blocked_by=[blocker["id"]])
        reassigned = cards.assign_agent(self.ws, card["id"], "claude")
        self.assertEqual("claude", reassigned["assigned_agent"])
        self.assertEqual([blocker["id"]], reassigned["blocked_by"])

    def test_list_cards_filters_by_assigned_agent_case_insensitively_and_exactly(self) -> None:
        cards.create_card(self.ws, "For codex", assigned_agent="codex")
        cards.create_card(self.ws, "For codex review", assigned_agent="codex-review")
        matched = server.list_cards(self.ws, assigned_agent="CODEX")
        self.assertEqual(["For codex"], [c["title"] for c in matched])


class UpdateCardTests(McpCase):
    def test_editing_title_and_priority_preserves_the_agent_marker(self) -> None:
        card = cards.create_card(self.ws, "Write the release notes", assigned_agent="claude")
        updated = cards.update_card(self.ws, card["id"], title="Write the v1.0 release notes", priority="High")
        self.assertEqual("Write the v1.0 release notes", updated["title"])
        self.assertEqual("claude", updated["assigned_agent"])

    def test_update_card_has_no_assignee_parameter(self) -> None:
        """Real tracker identity is reserved for a confirmed sync -- never
        this local-only path. Enforced by the function's own signature."""
        self.assertNotIn("assignee", inspect.signature(cards.update_card).parameters)


class SyncTokenTests(McpCase):
    def test_preview_sync_sends_nothing_and_returns_a_token(self) -> None:
        cards.create_card(self.ws, "Backend API")
        preview = server.preview_sync(self.ws)
        self.assertEqual(1, preview["creates"])
        self.assertTrue(preview["token"])
        self.assertEqual({}, FakeProvider._created)

    def test_confirm_sync_refuses_an_invented_token(self) -> None:
        cards.create_card(self.ws, "Backend API")
        server.preview_sync(self.ws)
        with self.assertRaises(ProviderError):
            server.confirm_sync(self.ws, "not-a-real-token")

    def test_confirm_sync_with_the_real_token_sends_and_the_agent_marker_survives(self) -> None:
        parent = cards.create_card(self.ws, "Backend API")
        cards.create_card(self.ws, "Frontend UI", blocked_by=[parent["id"]], assigned_agent="codex")
        preview = server.preview_sync(self.ws)
        server.confirm_sync(self.ws, preview["token"])

        after = server.list_cards(self.ws)
        self.assertTrue(all(c["key"].startswith("DEMO-") for c in after), after)
        child_after = next(c for c in after if c["title"] == "Frontend UI")
        self.assertEqual("codex", child_after["assigned_agent"])

    def test_a_token_is_invalidated_by_the_mutation_that_follows_it(self) -> None:
        cards.create_card(self.ws, "Backend API")
        preview = server.preview_sync(self.ws)
        cards.create_card(self.ws, "A second card issued after the preview")
        with self.assertRaises(ProviderError):
            server.confirm_sync(self.ws, preview["token"])

    def test_a_token_cannot_be_reused_after_it_is_consumed(self) -> None:
        cards.create_card(self.ws, "Backend API")
        preview = server.preview_sync(self.ws)
        server.confirm_sync(self.ws, preview["token"])
        with self.assertRaises(ProviderError):
            server.confirm_sync(self.ws, preview["token"])

    def test_a_concurrent_mutation_before_the_sync_lock_invalidates_the_token(self) -> None:
        cards.create_card(self.ws, "Backend API")
        preview = server.preview_sync(self.ws)
        real_sync = workspace_sync.sync

        def sync_after_concurrent_mutation(*args: Any, **kwargs: Any) -> Any:
            cards.create_card(self.ws, "Added while confirm_sync is waiting for the lock")
            return real_sync(*args, **kwargs)

        with (
            patch.object(workspace_sync, "sync", side_effect=sync_after_concurrent_mutation),
            self.assertRaises(ProviderError),
        ):
            server.confirm_sync(self.ws, preview["token"])

        self.assertEqual({}, FakeProvider._created)

    def test_an_empty_workspace_previews_as_nothing_to_send(self) -> None:
        preview = server.preview_sync(self.ws)
        self.assertEqual(0, preview["creates"])
        self.assertEqual(0, preview["pushes"])


class ReadToolTests(McpCase):
    def test_list_workspaces_finds_the_registered_workspace(self) -> None:
        found = server.list_workspaces()
        self.assertTrue(any(Path(w["path"]) == self.workspace.resolve() for w in found))

    def test_list_cards_across_every_registered_workspace(self) -> None:
        cards.create_card(self.ws, "In the default workspace")
        found = server.list_cards()
        self.assertTrue(any(c["title"] == "In the default workspace" for c in found))

    def test_get_card_resolves_by_id(self) -> None:
        created = cards.create_card(
            self.ws,
            "Findable card",
            issue_type="Story",
            body="The complete Markdown body.",
            labels=["mcp", "detail"],
        )
        cards.update_card(self.ws, created["id"], priority="High", due="2026-09-15")
        found = server.get_card(self.ws, created["id"])
        self.assertEqual("Findable card", found["title"])
        self.assertEqual("The complete Markdown body.", found["body"])
        self.assertEqual("Story", found["issue_type"])
        self.assertEqual("High", found["priority"])
        self.assertEqual("2026-09-15", found["due"])
        self.assertEqual(["mcp", "detail"], found["labels"])

    def test_get_card_raises_for_an_unknown_reference(self) -> None:
        with self.assertRaises(ProviderError):
            server.get_card(self.ws, "no-such-card")

    def test_list_dependencies_reports_blockers_and_assignment_together(self) -> None:
        blocker = cards.create_card(self.ws, "Backend API")
        card = cards.create_card(self.ws, "Frontend UI", blocked_by=[blocker["id"]], assigned_agent="codex")
        deps = server.list_dependencies(self.ws, card["id"])
        self.assertEqual([blocker["id"]], deps["blocked_by"])
        self.assertEqual("codex", deps["assigned_agent"])


if __name__ == "__main__":
    unittest.main()
