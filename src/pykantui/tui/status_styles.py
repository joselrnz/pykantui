"""Theme-aware presentation of provider-neutral workflow states."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from rich.style import Style
from rich.text import Text
from textual.color import Color, ColorParseError

from pykantui.tracker.models import ColumnGroup


class StatusThemeVariable(StrEnum):
    """Textual colour tokens used for workflow semantics.

    These are base theme variables rather than generated shades, so every
    bundled, built-in, light, dark and ANSI theme supplies a concrete value.
    """

    SECONDARY = "secondary"
    PRIMARY = "primary"
    ACCENT = "accent"
    WARNING = "warning"
    SUCCESS = "success"
    ERROR = "error"
    FOREGROUND = "foreground"

    @property
    def token(self) -> str:
        """Return the TCSS spelling of this variable."""
        return f"${self.value}"


_STATUS_VARIABLES: Mapping[ColumnGroup, StatusThemeVariable] = {
    ColumnGroup.BACKLOG: StatusThemeVariable.PRIMARY,
    ColumnGroup.TODO: StatusThemeVariable.WARNING,
    ColumnGroup.STARTED: StatusThemeVariable.SECONDARY,
    ColumnGroup.REVIEW: StatusThemeVariable.ACCENT,
    ColumnGroup.DONE: StatusThemeVariable.SUCCESS,
    ColumnGroup.CANCELLED: StatusThemeVariable.ERROR,
    ColumnGroup.UNKNOWN: StatusThemeVariable.FOREGROUND,
}

WORKFLOW_STATUS_CLASSES: frozenset[str] = frozenset(
    f"workflow-status-{variable.value}" for variable in StatusThemeVariable
)


def _column_group(value: ColumnGroup | str) -> ColumnGroup:
    try:
        return ColumnGroup(value)
    except (TypeError, ValueError):
        return ColumnGroup.UNKNOWN


def status_theme_variable(group: ColumnGroup | str) -> StatusThemeVariable:
    """Return the semantic theme variable for ``group``.

    Unknown plugin values deliberately receive the neutral foreground token;
    provider vocabulary never leaks into this presentation layer.
    """
    return _STATUS_VARIABLES[_column_group(group)]


def workflow_status_class(group: ColumnGroup | str) -> str:
    """Return the TCSS class for a normalized workflow group."""
    return f"workflow-status-{status_theme_variable(group).value}"


def resolve_status_color(group: ColumnGroup | str, theme_variables: Mapping[str, str]) -> Color:
    """Resolve a workflow group against the active Textual theme safely."""
    variable = status_theme_variable(group)
    raw = theme_variables.get(variable.value) or theme_variables.get(StatusThemeVariable.FOREGROUND.value)
    if raw:
        try:
            return Color.parse(raw)
        except ColorParseError:
            pass
    return Color.parse("ansi_default")


def workflow_status_text(
    label: str,
    group: ColumnGroup | str,
    theme_variables: Mapping[str, str],
) -> Text:
    """Build a single-line Rich status cell that truncates without wrapping."""
    color = resolve_status_color(group, theme_variables)
    return Text(
        label,
        style=Style(color=color.rich_color),
        overflow="ellipsis",
        no_wrap=True,
    )
