"""Asana card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "name",
    Field.BODY: "notes",
    Field.COLUMN: "memberships",
    Field.ASSIGNEE: "assignee",
    Field.DUE_DATE: "due_on",
}
CARD_FIELDS = card_schema(NATIVE_FIELDS, editable=tuple(NATIVE_FIELDS), creatable=tuple(NATIVE_FIELDS))
FILTER_LABELS = ProviderFilterLabels(
    scope="Project", status="Section", key="Task GID", key_placeholder="e.g. 1200123456789"
)
