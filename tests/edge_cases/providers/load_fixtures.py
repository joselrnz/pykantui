"""Deterministic, network-free fixtures shared by provider scale tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from pykantui.models import Task
from pykantui.tracker.base import Provider
from pykantui.tracker.models import (
    ColumnGroup,
    IssueDraft,
    IssueEdit,
    RemoteColumn,
    RemoteIssue,
    RemoteProject,
    RemoteUser,
)
from pykantui.tracker.registry import specs
from pykantui.tracker.spec import ProviderSpec
from pykantui.workspace import markdown
from pykantui.workspace.disk import OnDisk
from pykantui.workspace.state import SyncState

CARD_COUNT = 1_000
PROVIDER_NAMES = {
    "asana",
    "clickup",
    "forgejo",
    "github",
    "jira",
    "linear",
    "monday",
    "plane",
    "shortcut",
    "trello",
}
BASE_TIME = datetime(2026, 1, 1, 12, tzinfo=UTC)
TASK_BASE_TIME = datetime(2024, 1, 1, 12)
TODO = RemoteColumn(column_id="todo", name="To Do", position=0, group=ColumnGroup.TODO)
DONE = RemoteColumn(column_id="done", name="Done", position=1, group=ColumnGroup.DONE)
COLUMNS = [TODO, DONE]


class MatrixProvider(Provider):
    """A provider-spec host whose only data source is deterministic memory."""

    spec: ClassVar[ProviderSpec] = specs()[0]

    def __init__(
        self,
        provider_spec: ProviderSpec,
        issues: list[RemoteIssue],
        *,
        config: dict[str, object] | None = None,
    ) -> None:
        super().__init__(config or {}, {})
        # Tests execute providers serially. Mutating this test double's class
        # contract avoids changing production specs or constructing clients.
        type(self).spec = provider_spec
        self._issues = {issue.issue_id: issue for issue in issues}
        self.remote_fetches = 0
        self.updates: list[tuple[str, IssueEdit]] = []
        self.creates: list[IssueDraft] = []

    def verify(self) -> RemoteUser:
        return RemoteUser(account_id="me", display_name="Matrix User")

    def list_projects(self) -> list[RemoteProject]:
        return [project_for(self.spec)]

    def list_columns(self, project_id: str) -> list[RemoteColumn]:
        del project_id
        return list(COLUMNS)

    def iter_issues(self, project_id: str) -> Iterator[RemoteIssue]:
        del project_id
        return iter(self._issues.values())

    def get_issue(self, project_id: str, issue: RemoteIssue) -> RemoteIssue | None:
        del project_id
        self.remote_fetches += 1
        return self._issues.get(issue.issue_id)

    def update_issue(self, issue: RemoteIssue, edit: IssueEdit) -> None:
        self.reject_unsupported(edit)
        self.updates.append((issue.issue_id, edit))

    def create_issue(self, project_id: str, draft: IssueDraft) -> RemoteIssue:
        del project_id
        self.creates.append(draft)
        sequence = len(self.creates)
        return RemoteIssue(
            issue_id=f"created-{self.spec.name}-{sequence:04d}",
            key=f"{self.spec.name.upper()}-NEW-{sequence:04d}",
            title=draft.title,
            body=draft.body,
            column_id=draft.column_id or TODO.column_id,
            status=draft.column_name or TODO.name,
            due_date=draft.due_date,
        )


def project_for(spec: ProviderSpec) -> RemoteProject:
    return RemoteProject(
        project_id=f"{spec.name}-project",
        key=spec.name.upper(),
        name=f"{spec.label} scale board",
    )


def issues_for(spec: ProviderSpec) -> list[RemoteIssue]:
    """Return exactly 1,000 stable issues; no randomness or wall clock."""
    return [
        RemoteIssue(
            issue_id=f"{spec.name}-{index:04d}",
            key=f"{spec.name.upper()}-{index:04d}",
            title=f"{spec.label} item {index:04d}",
            body=f"Body {index:04d}",
            column_id=TODO.column_id,
            status=TODO.name,
            created_at=BASE_TIME + timedelta(minutes=index),
        )
        for index in range(CARD_COUNT)
    ]


def planning_fixture(issues: list[RemoteIssue]) -> tuple[dict[str, OnDisk], SyncState]:
    """Make title, move, and combined edits in a fixed 3/10 pattern."""
    entries: dict[str, OnDisk] = {}
    state = SyncState({issue.issue_id: issue for issue in issues})
    for index, issue in enumerate(issues):
        mode = index % 10
        title = f"Edited {issue.title}" if mode in {0, 2} else issue.title
        folder = "done" if mode in {1, 2} else "to-do"
        entries[issue.issue_id] = OnDisk(
            path=Path(folder) / issue.filename(),
            column_name=folder,
            file=markdown.IssueFile(
                {"id": issue.issue_id, "key": issue.key, "title": title},
                issue.body,
                "",
            ),
        )
    return entries, state


def drafts_for(spec: ProviderSpec, workspace: Path, *, count: int = 100) -> dict[str, OnDisk]:
    """Build many local create drafts inside the throwaway workspace."""
    return {
        issue_id: OnDisk(
            path=workspace / "drafts" / f"{issue_id}.md",
            column_name="to-do",
            file=markdown.IssueFile(
                {
                    "id": issue_id,
                    "title": f"New {spec.label} card {index:03d}",
                    "due": "2026-09-01",
                },
                f"Create-path stress body {index:03d}",
                f"Private note {index:03d}",
            ),
        )
        for index in range(count)
        for issue_id in (f"draft-{spec.name}-scale-{index:03d}",)
    }


def tasks_for(spec: ProviderSpec, *, count: int = CARD_COUNT) -> list[Task]:
    """Build provider-shaped UI cards with a predictable filtered subset."""
    project = project_for(spec)
    today = date.today()
    return [
        Task(
            task_id=index + 1,
            title=("render-visible " if index % 10 == 0 else "")
            + ("release-target " if index % 20 == 0 else "ordinary ")
            + f"{spec.name} {index:04d}",
            column_id=1 if index % 3 else 2,
            position=count - index,
            description="   " if index % 6 == 0 else f"Notes {index:04d}",
            created_at=TASK_BASE_TIME + timedelta(days=index),
            due_date=(today - timedelta(days=2), today, None, today + timedelta(days=5))[index % 4],
            blocked_by=[count + 1] if index % 7 == 0 else [],
            metadata={
                "id": f"{spec.name}-{index:04d}",
                "key": f"{spec.name.upper()}-{index:04d}",
                "project": "OTHER" if index % 17 == 0 else project.key,
                "assignee": "Alex" if index % 2 == 0 else "Sam",
                "issue_type": "Bug" if index % 3 == 0 else "Task",
                "priority": ("Medium", "Highest", "Unknown", "Low", "High")[index % 5],
                "labels": ["backend", "release"] if index % 4 == 0 else ["ui"],
            },
        )
        for index in range(count)
    ]
