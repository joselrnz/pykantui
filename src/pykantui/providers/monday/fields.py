"""Monday.com card-field contract and board-column mapping keys."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "name",
    Field.BODY: "long_text",
    Field.COLUMN: "status",
    Field.ASSIGNEE: "people",
    Field.ISSUE_TYPE: "dropdown",
    Field.PRIORITY: "status",
    Field.LABELS: "dropdown",
    Field.DUE_DATE: "date",
}
CONFIGURED_BY = {
    Field.BODY: "description_column",
    Field.ASSIGNEE: "assignee_column",
    Field.ISSUE_TYPE: "type_column",
    Field.PRIORITY: "priority_column",
    Field.LABELS: "labels_column",
    Field.DUE_DATE: "due_column",
}
CARD_FIELDS = card_schema(
    NATIVE_FIELDS,
    editable=tuple(NATIVE_FIELDS),
    creatable=tuple(NATIVE_FIELDS),
    configured_by=CONFIGURED_BY,
)
FILTER_LABELS = ProviderFilterLabels(
    scope="Board", status="Status", assignee="People", key="Item ID", key_placeholder="e.g. 123456789"
)
