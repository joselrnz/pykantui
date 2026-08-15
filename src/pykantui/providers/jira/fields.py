"""Jira card-field contract."""

from pykantui.tracker.fields import CardFieldName as Field
from pykantui.tracker.fields import card_schema
from pykantui.tracker.filter_fields import ProviderFilterLabels

NATIVE_FIELDS = {
    Field.TITLE: "summary",
    Field.BODY: "description",
    Field.COLUMN: "status",
    Field.ASSIGNEE: "assignee",
    Field.ISSUE_TYPE: "issuetype",
    Field.PRIORITY: "priority",
    Field.LABELS: "labels",
    Field.COMPONENTS: "components",
    Field.DUE_DATE: "duedate",
}
CARD_FIELDS = card_schema(
    NATIVE_FIELDS,
    editable=tuple(NATIVE_FIELDS),
    creatable=tuple(NATIVE_FIELDS),
    required=(Field.TITLE, Field.ISSUE_TYPE),
    not_clearable=(Field.TITLE, Field.ISSUE_TYPE),
)
FILTER_LABELS = ProviderFilterLabels(
    scope="Project",
    status="Status",
    key="Issue Key",
    key_placeholder="e.g. SCRUM-25",
    sprint="Active Sprint",
)
