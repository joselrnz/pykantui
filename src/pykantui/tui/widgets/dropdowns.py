"""Labelled dropdowns for the filter panel.

The shape is borrowed from jiratui (see ``reference/jiratui``): a compact
``Select`` with a rounded border, the field name in the border title and its
shortcut key in the border subtitle. It reads as a labelled form field rather
than a bare widget, and there is nowhere for the label to drift away from the
control it names.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from textual.widgets import Button, Checkbox, Input, Select

from pykantui.core.filters import SORT_LABELS, STATE_LABELS, FilterState, SortKey
from pykantui.i18n import translate as _
from pykantui.tui.glyphs import SEARCH_GLYPH


class LabelledSelect(Select[Any]):
    """A ``Select`` that carries its own label and key hint."""

    def __init__(
        self,
        *,
        options: list[tuple[str, Any]],
        prompt: str,
        title: str,
        key: str,
        widget_id: str,
        # Select.NULL, not Select.BLANK: in Textual 8.2 BLANK is the literal
        # False and is rejected by the value validator. NULL is the sentinel.
        value: Any = Select.NULL,
    ) -> None:
        super().__init__(
            options=options,
            prompt=prompt,
            value=value,
            id=widget_id,
            type_to_search=True,
            compact=True,
            classes="dropdown",
        )
        if key == "*":
            self.add_class("required")
        self.border_title = title
        self.border_subtitle = f"({key})" if key else ""


class LabelledInput(Input):
    """The search box, wearing the same label and hint as the dropdowns."""

    def __init__(self, *, placeholder: str, title: str, key: str, widget_id: str) -> None:
        super().__init__(placeholder=placeholder, id=widget_id, compact=True, classes="dropdown")
        if key == "*":
            self.add_class("required")
        self.border_title = title
        # An empty key means no hint at all, for fields that have no shortcut.
        self.border_subtitle = f"({key})" if key else ""


def state_select() -> LabelledSelect:
    return LabelledSelect(
        options=[(_(STATE_LABELS[state]), state.value) for state in FilterState],
        prompt=_("Any state"),
        title=_("State"),
        # (y): Status owns (s), and (e) is the card edit key.
        key="y",
        widget_id="filter-state",
    )


def sort_select() -> LabelledSelect:
    return LabelledSelect(
        options=[(_(SORT_LABELS[key]), key.value) for key in SortKey],
        prompt=_("Manual"),
        title=_("Sort"),
        key="o",
        widget_id="filter-sort",
        value=SortKey.MANUAL.value,
    )


def saved_select(names: list[str]) -> LabelledSelect:  # noqa: D103
    return LabelledSelect(
        options=[(name, name) for name in names],
        prompt=_("Saved filter"),
        title=_("Saved"),
        # (g), not (v): Active Sprint owns (v), matching jiratui.
        key="g",
        widget_id="filter-saved",
    )


#: Shortcuts for the two primary provider fields. Priority and labels remain
#: reachable by tab and the Filter menu; assigning them board-navigation keys
#: would make ordinary card movement unexpectedly open the filter bar.
PROVIDER_KEYS = {"assignee": "a", "issue_type": "t", "priority": "", "labels": ""}


def provider_select(field: str, label: str, values: list[str]) -> LabelledSelect:
    select = LabelledSelect(
        options=[(value, value) for value in values],
        prompt=_("Any {field}").format(field=_(label).lower()),
        title=_(label),
        key=PROVIDER_KEYS[field],
        widget_id=f"filter-provider-{field}",
    )
    select.add_class("provider-filter")
    return select


def search_input() -> LabelledInput:
    return LabelledInput(placeholder=_("title or notes…"), title=_("Search"), key="/", widget_id="bar-search")


class DateInput(LabelledInput):
    """A date field. Invalid text is simply not a filter, rather than an error."""

    def __init__(self, *, title: str, key: str, widget_id: str) -> None:
        super().__init__(placeholder="YYYY-MM-DD", title=title, key=key, widget_id=widget_id)

    @staticmethod
    def parse(raw: str) -> date | None:
        try:
            return date.fromisoformat(raw.strip())
        except ValueError:
            return None


class LabelledCheckbox(Checkbox):
    """A checkbox with a shortcut hint.

    Only a subtitle: the checkbox already draws its own label, so a border title
    would say the same thing twice. jiratui does the same.
    """

    def __init__(self, *, label: str, key: str, widget_id: str, value: bool = False) -> None:
        super().__init__(label=label, value=value, id=widget_id, classes="input-checkbox")
        self.border_subtitle = f"({key})"


def project_select(projects: list[str], *, title: str = "Project") -> LabelledSelect:
    select = LabelledSelect(
        options=[(project, project) for project in projects],
        prompt=_("Any {field}").format(field=_(title).lower()),
        title=_(title),
        key="p",
        widget_id="filter-project",
    )
    select.add_class("provider-filter")
    return select


def status_select(columns: list[tuple[str, int]], *, title: str = "Status", provider: bool = False) -> LabelledSelect:
    """The board's columns — our equivalent of a Jira status."""
    select = LabelledSelect(
        options=[(name, str(column_id)) for name, column_id in columns],
        prompt=_("Any {field}").format(field=_(title).lower()),
        title=_(title),
        key="s",
        widget_id="filter-status",
    )
    if provider:
        select.add_class("provider-filter")
    return select


def key_input(
    *,
    title: str = "Work Item Key",
    placeholder: str = "e.g. ITEM-123",
    provider: bool = False,
) -> LabelledInput:
    field = LabelledInput(
        # (w), not (k): k moves the focus up on the board.
        placeholder=placeholder,
        title=_(title),
        key="w",
        widget_id="filter-key",
    )
    if provider:
        field.add_class("provider-filter")
    return field


def created_from_input() -> DateInput:
    return DateInput(title=_("Created From"), key="f", widget_id="filter-created-from")


def created_until_input() -> DateInput:
    return DateInput(title=_("Created Until"), key="u", widget_id="filter-created-until")


def active_sprint_checkbox(label: str, value: bool = False, *, enabled: bool = True) -> LabelledCheckbox:
    # (x), not (v): v opens a card's detail.
    box = LabelledCheckbox(label=label, key="x", widget_id="filter-sprint", value=value)
    box.add_class("provider-filter")
    box.disabled = not enabled
    if not enabled:
        box.tooltip = _("Live provider queries are unavailable in this workspace")
    return box


def query_input(title: str, language: str, value: str = "", *, enabled: bool = True) -> LabelledInput:
    field = LabelledInput(
        placeholder=_("Type in a {language} expression to search issues…").format(language=language),
        title=_(title),
        # (q), not (j): j moves the focus down on the board.
        key="q",
        widget_id="filter-query",
    )
    field.value = value
    field.add_class("provider-filter")
    field.disabled = not enabled
    if not enabled:
        field.tooltip = _("Live provider queries are unavailable in this workspace")
    return field


def search_button(*, enabled: bool = True) -> Button:
    """Re-runs the query. Only Jira needs it: a local board filters as you type."""
    button = Button(
        f"{SEARCH_GLYPH} {_('Search')}",
        id="filter-search",
        variant="primary",
        flat=True,
        compact=True,
    )
    button.disabled = not enabled
    if not enabled:
        button.tooltip = _("Live provider queries are unavailable in this workspace")
    return button
