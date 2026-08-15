"""Trello card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "name",
    Field.BODY: "desc",
    Field.COLUMN: "idList",
    Field.ASSIGNEE: "idMembers",
    Field.LABELS: "idLabels",
    Field.DUE_DATE: "due",
}
CARD_FIELDS = card_schema(NATIVE_FIELDS, editable=tuple(NATIVE_FIELDS), creatable=tuple(NATIVE_FIELDS))
FILTER_LABELS = ProviderFilterLabels(
    scope="Board", status="List", assignee="Member", key="Card ID", key_placeholder="e.g. 65a1b2c3d4"
)
