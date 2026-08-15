"""Read-only Split-pane rendering for one selected work item."""

from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Static

from pykantui.models import Task
from pykantui.tui.provider_links import ProviderIssueLink, provider_issue_url
from pykantui.tui.widgets import card_fields
from pykantui.tui.widgets.work_item_fields import detail_field_visible
from pykantui.tui.widgets.work_item_table import DetailField, WorkItemRowsBase


class WorkItemDetailBase(WorkItemRowsBase):
    """Render provider-neutral card details outside the interaction shell."""

    def _clear_detail(self) -> None:
        """Remove the previous card when filtering leaves no selected row."""
        for row in card_fields.ROWS:
            for field in row:
                detail = self.query_one(
                    f"#work-item-{field.key.replace('_', '-')}",
                    DetailField,
                )
                detail.update("—")
                detail.display = False
                if field.key == "status":
                    detail.apply_status_group("unknown")
                elif field.key == "issue_type":
                    detail.apply_item_type("")
        for field_row in self.query(".work-item-field-row"):
            field_row.display = False
        for selector in (
            "#work-item-sync",
            "#work-item-info-summary",
            "#work-item-description",
            "#work-item-private-notes",
            "#work-item-related",
            "#work-item-links",
            "#work-item-subtasks",
        ):
            self.query_one(selector, Static).update("—")
        self.query_one("#work-item-provider-link", ProviderIssueLink).set_provider_url("")

    def _render_detail(self, task: Task) -> None:
        columns = self.app.column_choices()
        blockers = self.app.backend.get_tasks_by_ids(task.blocked_by)
        available = self.app.backend.available_task_fields()
        field_rows = list(self.query(".work-item-field-row"))
        for row, field_row in zip(card_fields.ROWS, field_rows, strict=True):
            row_visible = False
            for field in row:
                value = card_fields.value_of(field, task, columns, blockers) or "—"
                detail = self.query_one(
                    f"#work-item-{field.key.replace('_', '-')}",
                    DetailField,
                )
                detail.update(value)
                detail.display = detail_field_visible(field, value=value, available=available)
                row_visible = row_visible or bool(detail.display)
                if field.key == "status":
                    detail.apply_status_group(self.app.backend.column_group(task.column_id))
                elif field.key == "issue_type":
                    detail.apply_item_type(value)
            if isinstance(field_row, Horizontal):
                field_row.display = row_visible

        status = self._status(task)
        provider = self.app.backend.display_kind()
        sync_text = status.markup() if status is not None else "local board"
        self.query_one("#work-item-sync", Static).update(
            f"{sync_text}  ·  compared with the last {provider} sync"
        )
        policy = self.app.editor_policy()
        description = self.query_one("#work-item-description", Static)
        description.border_title = policy.description_title
        description.update(task.description or "—")
        self.query_one("#work-item-private-notes", Static).update(
            str(task.metadata.get("private_notes", "") or "—")
        )
        self.query_one("#work-item-info-summary", Static).update(task.title.splitlines()[-1])
        self.query_one("#work-item-provider-link", ProviderIssueLink).set_provider_url(provider_issue_url(task))
        blocker_names = [item.title.splitlines()[-1] for item in blockers]
        self.query_one("#work-item-related", Static).update(
            "Blocked by: " + ", ".join(blocker_names) if blocker_names else "No local blockers"
        )
        self.query_one("#work-item-links", Static).update(
            str(task.metadata.get("url", "") or "No provider link cached")
        )
        subtasks = task.metadata.get("subtasks")
        self.query_one("#work-item-subtasks", Static).update(
            display_value(subtasks, empty="No cached subtasks")
        )


def display_value(value: object, *, empty: str) -> str:
    """Format cached provider lists without exposing representation syntax."""
    if not value:
        return empty
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)
