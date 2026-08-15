"""Plane card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "name",
    Field.BODY: "description_html",
    Field.COLUMN: "state",
    Field.ASSIGNEE: "assignees",
    Field.PRIORITY: "priority",
    Field.LABELS: "labels",
    Field.DUE_DATE: "target_date",
}
CARD_FIELDS = card_schema(NATIVE_FIELDS, editable=tuple(NATIVE_FIELDS), creatable=tuple(NATIVE_FIELDS))
FILTER_LABELS = ProviderFilterLabels(
    scope="Project", status="State", key="Work Item ID", key_placeholder="e.g. PROJ-123"
)
