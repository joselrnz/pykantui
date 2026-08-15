"""Closed sorting vocabulary shared by filters, menus, and table headers."""

from enum import StrEnum


class SortKey(StrEnum):
    """A deterministic provider-neutral work-item ordering."""

    MANUAL = "manual"
    TITLE = "title"
    KEY = "key"
    STATUS = "status"
    TYPE = "type"
    ASSIGNEE = "assignee"
    REPORTER = "reporter"
    DUE = "due"
    CREATED = "created"
    AGE = "age"
    PRIORITY = "priority"
    LABELS = "labels"
    COMPONENTS = "components"


SORT_LABELS = {
    SortKey.MANUAL: "Manual",
    SortKey.TITLE: "Title",
    SortKey.KEY: "Key",
    SortKey.STATUS: "Status",
    SortKey.TYPE: "Type",
    SortKey.ASSIGNEE: "Assignee",
    SortKey.REPORTER: "Reporter",
    SortKey.DUE: "Due",
    SortKey.CREATED: "Created",
    SortKey.AGE: "Age",
    SortKey.PRIORITY: "Priority",
    SortKey.LABELS: "Labels",
    SortKey.COMPONENTS: "Components",
}


__all__ = ["SORT_LABELS", "SortKey"]
