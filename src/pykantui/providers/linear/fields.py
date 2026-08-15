"""Linear card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "title",
    Field.BODY: "description",
    Field.COLUMN: "stateId",
    Field.ASSIGNEE: "assigneeId",
    Field.PRIORITY: "priority",
    Field.LABELS: "labelIds",
    Field.DUE_DATE: "dueDate",
}
CARD_FIELDS = card_schema(NATIVE_FIELDS, editable=tuple(NATIVE_FIELDS), creatable=tuple(NATIVE_FIELDS))
FILTER_LABELS = ProviderFilterLabels(scope="Team", status="Status", key="Issue ID", key_placeholder="e.g. ENG-123")
