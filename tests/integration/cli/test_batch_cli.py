"""The public declarative batch command."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from pykantui.cli import main
from pykantui.tracker.base import Provider
from pykantui.tracker.models import IssueDraft, IssueType, RemoteColumn, RemoteIssue, RemoteProject, RemoteUser
from pykantui.tracker.spec import Capabilities, ProviderSpec


class CliProvider(Provider):
    spec = ProviderSpec(
        name="jira",
        label="Jira",
        capabilities=Capabilities(
            create_issues=True,
            move_issues=True,
            parent_issues=True,
            writable_fields=("title", "body", "column_id", "issue_type"),
        ),
    )

    def __init__(self) -> None:
        super().__init__({}, {})
        self.remote: dict[str, RemoteIssue] = {}

    def list_projects(self) -> list[RemoteProject]:
        return []

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me", display_name="Me")

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        del project_id
        return [RemoteColumn(column_id="todo", name="To Do")]

    def list_issue_types(self, project_id: str) -> list[IssueType]:
        del project_id
        return [IssueType(type_id="story", name="Story")]

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        del project_id
        return iter(self.remote.values())

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        del project_id
        issue = RemoteIssue(
            issue_id="1",
            key="PAY-1",
            title=draft.title,
            body=draft.body,
            issue_type=draft.issue_type,
            column_id="todo",
            status="To Do",
        )
        self.remote[issue.key] = issue
        return issue

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        del project_id
        return self.remote.get(issue.key)


class CliProject:
    provider = "jira"
    project_id = "100"
    key = "PAY"
    name = "Payments"

    def __init__(self, provider: CliProvider) -> None:
        self._provider = provider

    def open(self) -> CliProvider:
        return self._provider

    def remote(self) -> RemoteProject:
        return RemoteProject(project_id=self.project_id, key=self.key, name=self.name)


class BatchCliTests(unittest.TestCase):
    def test_jira_shorthand_generates_ten_issues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "issues.yml"
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["batch", "jira", "--count", "10", "-o", str(target)])

            contents = target.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("generated 10 Jira issue definitions", out.getvalue())
        self.assertIn("apiVersion: pykantui.dev/v1alpha1", contents)
        self.assertIn("ref: issue-10", contents)

    def test_generator_refuses_to_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "issues.yml"
            target.write_text("mine", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["batch", "jira", "-o", str(target)])

            contents = target.read_text(encoding="utf-8")

        self.assertEqual(code, 2)
        self.assertIn("already exists", err.getvalue())
        self.assertEqual(contents, "mine")

    def test_plan_then_apply_uses_saved_reviewed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            manifest = workspace / "issues.yml"
            plan = workspace / "issues.plan.json"
            manifest.write_text(
                """apiVersion: pykantui.dev/v1alpha1
kind: IssueBatch
metadata:
  name: cli-batch
target:
  provider: jira
  project: PAY
defaults:
  type: Story
  state: To Do
issues:
  - ref: one
    title: CLI issue
""",
                encoding="utf-8",
            )
            provider = CliProvider()
            project = CliProject(provider)
            out, err = io.StringIO(), io.StringIO()
            with (
                patch("pykantui.commands.batch.Project.load", return_value=project),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                planned = main(
                    ["batch", "plan", str(manifest), "-o", str(plan), "--path", str(workspace)]
                )
                applied = main(
                    ["batch", "apply", str(plan), "--path", str(workspace), "--yes"]
                )

            self.assertEqual(planned, 0)
            self.assertEqual(applied, 0)
            self.assertIn("READY TO CREATE (1)", out.getvalue())
            self.assertIn("Applied batch: created 1, completed 1", out.getvalue())
            self.assertEqual(list(provider.remote), ["PAY-1"])
            self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
