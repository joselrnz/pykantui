"""Shortcut card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "name",
    Field.BODY: "description",
    Field.COLUMN: "workflow_state_id",
    Field.ASSIGNEE: "owner_ids",
    Field.ISSUE_TYPE: "story_type",
    Field.LABELS: "labels",
    Field.DUE_DATE: "deadline",
}
CARD_FIELDS = card_schema(NATIVE_FIELDS, editable=tuple(NATIVE_FIELDS), creatable=tuple(NATIVE_FIELDS))
FILTER_LABELS = ProviderFilterLabels(
    scope="Workflow",
    status="Workflow State",
    assignee="Owner",
    issue_type="Story Type",
    key="Story ID",
    key_placeholder="e.g. 12345",
)
