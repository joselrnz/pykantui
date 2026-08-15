"""Extraction markers for labels selected dynamically at runtime.

The application translates enum values and provider-neutral field labels by
looking them up at render time. Keeping those source strings here lets Babel
discover them without coupling the domain models to a specific language.
"""

from pykantui.i18n.translator import translate as _


def _messages_for_extraction() -> tuple[str, ...]:
    """Return dynamic message ids; this function is for Babel extraction."""
    return (
        _("Filter"),
        _("Sort"),
        _("Columns"),
        _("View"),
        _("Blocked"),
        _("Unblocked"),
        _("Overdue"),
        _("Due today"),
        _("No due date"),
        _("Has notes"),
        _("Manual"),
        _("Due"),
        _("Age"),
        _("Rows"),
        _("Split"),
        _("adjacent"),
        _("jump"),
        _("New folder"),
        _("Work Items"),
        _("Info"),
        _("Comments"),
        _("Related"),
        _("Attachments"),
        _("Links"),
        _("Subtasks"),
        _("Project"),
        _("Sprint"),
        _("Query"),
        _("Reporter"),
        _("Created"),
        _("Last Update"),
        _("Due Date"),
        _("Resolved"),
        _("Resolution"),
        _("Blocked by"),
        _("Time Tracking"),
        _("Highest"),
        _("Blocker"),
        _("Critical"),
        _("High"),
        _("Medium"),
        _("Normal"),
        _("Low"),
        _("Lowest"),
        _("Trivial"),
    )
