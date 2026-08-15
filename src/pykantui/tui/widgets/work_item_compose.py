"""Declarative child layout for the rows and split work-item view."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, Static, TabbedContent, TabPane

from pykantui.i18n import translate as _
from pykantui.tui.provider_links import ProviderIssueLink
from pykantui.tui.widgets import card_fields
from pykantui.tui.widgets.comments import CommentsPane
from pykantui.tui.widgets.work_item_table import DetailField, WorkItemTable


def compose_work_item_view() -> ComposeResult:
    """Yield the stable rows/split widget tree."""
    with Vertical(id="work-items-list-pane"):
        yield Label(f"{_('Work Items')} (0)", id="work-items-heading")
        yield WorkItemTable(id="work-items-table", cursor_type="row", zebra_stripes=True)
    yield Static("", id="work-item-resizer")
    with Vertical(id="work-item-detail-pane"):
        with TabbedContent(initial="work-item-details-tab", id="work-item-tabs"):
            with TabPane(f"{_('Info')} 2", id="work-item-info-tab"):
                yield Static("", id="work-item-sync")
                with VerticalScroll(id="work-item-info-read"):
                    with Horizontal(id="work-item-summary-row"):
                        yield Static("—", id="work-item-info-summary", classes="work-item-text", markup=False)
                        yield ProviderIssueLink(id="work-item-provider-link")
                    yield Static("—", id="work-item-description", classes="work-item-text", markup=False)
                    yield Static(
                        "—",
                        id="work-item-private-notes",
                        classes="work-item-text local-only",
                        markup=False,
                    )
                yield VerticalScroll(id="work-item-info-edit")
            with TabPane(f"{_('Details')} 3", id="work-item-details-tab"):
                with VerticalScroll(id="work-item-detail-scroll"):
                    for row in card_fields.ROWS:
                        with Horizontal(classes="work-item-field-row"):
                            for field in row:
                                yield DetailField(field)
                with VerticalScroll(id="work-item-edit-scroll"):
                    yield Vertical(id="work-item-detail-edit")
            with TabPane(f"{_('Comments')} 4", id="work-item-comments-tab"):
                yield CommentsPane("work-item")
            with TabPane(f"{_('Related')} 5", id="work-item-related-tab"):
                yield Static("—", id="work-item-related", classes="work-item-empty", markup=False)
            with TabPane(f"{_('Attachments')} 6", id="work-item-attachments-tab"):
                yield Static(_("Attachments are not stored in local Markdown."), classes="work-item-empty")
            with TabPane(f"{_('Links')} 7", id="work-item-links-tab"):
                yield Static("—", id="work-item-links", classes="work-item-empty", markup=False)
            with TabPane(f"{_('Subtasks')} 8", id="work-item-subtasks-tab"):
                yield Static("—", id="work-item-subtasks", classes="work-item-empty", markup=False)
        with Horizontal(id="work-item-edit-actions"):
            yield Button(_("Edit"), id="work-item-edit-start")
            yield Button(_("Save"), id="work-item-edit-save", variant="primary")
            yield Button(_("Cancel"), id="work-item-edit-cancel")
