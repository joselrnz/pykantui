"""Forgejo card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "title",
    Field.BODY: "body",
    Field.COLUMN: "labels/state",
    Field.ASSIGNEE: "assignees",
    Field.LABELS: "labels",
    Field.DUE_DATE: "due_date",
}
CARD_FIELDS = card_schema(NATIVE_FIELDS, editable=tuple(NATIVE_FIELDS), creatable=tuple(NATIVE_FIELDS))
FILTER_LABELS = ProviderFilterLabels(
    scope="Repository",
    status="State",
    key="Issue Number",
    key_placeholder="e.g. #123",
)

__all__ = ["CARD_FIELDS", "FILTER_LABELS"]
