"""ClickUp card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "name",
    Field.BODY: "description",
    Field.COLUMN: "status",
    Field.ASSIGNEE: "assignees",
    Field.ISSUE_TYPE: "custom_item_id",
    Field.PRIORITY: "priority",
    Field.LABELS: "tags",
    Field.DUE_DATE: "due_date",
}
CARD_FIELDS = card_schema(NATIVE_FIELDS, editable=tuple(NATIVE_FIELDS), creatable=tuple(NATIVE_FIELDS))
FILTER_LABELS = ProviderFilterLabels(
    scope="List", status="Status", labels="Tags", key="Task ID", key_placeholder="e.g. 86b1abc"
)
