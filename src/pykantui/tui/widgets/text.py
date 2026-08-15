"""Text widgets that do not take part in text selection.

Textual selects text when you press and drag across a widget. On a board that
fights the thing dragging is *for*: pressing a card and pulling it toward the
next column highlighted the words on every card it crossed instead of moving
the card.

Selection is disabled per leaf widget rather than for the whole app, so the
detail view stays selectable — copying an issue key out of it is useful.
"""

from __future__ import annotations

from textual.widgets import Label, Static


class BoardLabel(Label):
    """A label on the board. Drag it and you are dragging a card, not selecting."""

    ALLOW_SELECT = False


class BoardStatic(Static):
    """As :class:`BoardLabel`, for the block of body text on a card."""

    ALLOW_SELECT = False
