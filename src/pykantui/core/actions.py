"""What a click means.

Every clickable thing in the UI — a chip, a menu row, a dropdown — has to tell
the app what it stands for, and the only channel a Textual widget id or an
``OptionList`` option id gives you is a string. So there is a wire format:
``"kind:value"``, as in ``sort:due`` or ``act:clear``.

The point of this module is that the string is the *wire*, not the vocabulary.
It is parsed into an :class:`Action` at the boundary and never picked apart with
``partition(":")`` again, and every closed set of values behind it — which menus
exist, which column commands exist — is an enum the type checker can see. A
misspelled action is then a parse that returns ``None`` at one known place,
rather than an ``elif`` that silently never fires.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

from pykantui.i18n import translate as _

#: The one character separating a kind from its payload. Kinds may contain a
#: hyphen (``pick-jira``); payloads may contain anything but a colon.
SEPARATOR = ":"

#: Prefix on the widget id of a chip in the filter panel.
CHIP_PREFIX = "chip-"

_E = TypeVar("_E", bound=StrEnum)


class ActionKind(StrEnum):
    """The verb of an action. The payload's meaning depends on it."""

    OPEN = "open"
    """Open another menu. Payload: a :class:`Menu`."""

    STATE = "state"
    """Toggle a card state on or off. Payload: a ``FilterState``."""

    PICK_STATE = "pick-state"
    """Replace the card states with one, or none. Payload: a ``FilterState``."""

    PROJECT = "project"
    """Filter to a Jira project key. Empty payload clears it."""

    STATUS = "status"
    """Filter to one column. Payload: a column id, or empty to clear."""

    KEY = "key"
    """Filter by issue key or card id substring."""

    FROM = "from"
    """Lower bound on the created date. Payload: a date the UI can parse."""

    UNTIL = "until"
    """Upper bound on the created date."""

    SPRINT = "sprint"
    """Switch a Jira board between its sprint and its query. Payload: 0 or 1."""

    RUN = "run"
    """Re-ask the backend. Payload: the query text to run."""

    PICK_PROVIDER = "pick-provider"
    """Filter on Jira metadata. Payload: ``field=value``, empty value clears."""

    SORT = "sort"
    """Payload: a ``SortKey``."""

    SAVED = "saved"
    """Load a saved filter. Payload: its name."""

    FOCUS = "focus"
    """Move focus to a widget. Payload: its id."""

    ACT = "act"
    """A one-off command. Payload: an :class:`Act`."""

    VIEW = "view"
    """A view toggle. Payload: a :class:`ViewToggle`."""

    LAYOUT = "layout"
    """Rows, Kanban or Split. Payload: a :class:`BoardLayout`."""

    PANE = "pane"
    """Resize the list pane in Split. Payload: a :class:`PaneAdjustment`."""

    COL = "col"
    """Something to a column. Payload: a :class:`ColumnCommand`."""

    TABLE_COLUMN = "table-column"
    """Toggle a Rows/Split field. Payload: a ``WorkItemColumn``."""

    HELP = "help"
    """Show a help topic. Payload: a :class:`HelpTopic`."""


class Menu(StrEnum):
    """The menus reachable from the top bar."""

    MAIN = "main"
    FILTER = "filter"
    SORT = "sort"
    COLUMNS = "columns"
    VIEW = "view"
    HELP = "help"

    @property
    def label(self) -> str:
        """The heading, and the bar label where there is one."""
        if self is Menu.MAIN:
            return _("⌘ Menu")
        if self is Menu.VIEW:
            return _("▥ View")
        return _(self.value.title())

    @classmethod
    def in_bar(cls) -> list[Menu]:
        """High-frequency menus that deserve a persistent toolbar label.

        Columns and Help remain searchable in the application menu. Keeping
        them out of the quick row leaves room for search on ordinary terminals.
        """
        return [cls.FILTER, cls.SORT, cls.VIEW]


class Act(StrEnum):
    """A command that does something once, rather than toggling a setting."""

    REVERSE = "reverse"
    CLEAR = "clear"
    SAVE = "save"
    NEW = "new"
    REFRESH = "refresh"
    SYNC = "sync"
    PROJECTS = "projects"


class ViewToggle(StrEnum):
    """Something on the View menu."""

    MOVEMENT = "movement"
    CONFIRM = "confirm"
    DETAIL = "detail"
    COLLAPSE = "collapse"


class PaneAdjustment(StrEnum):
    """A bounded adjustment to the Split view's work-item pane."""

    NARROWER = "narrower"
    WIDER = "wider"
    RESET = "reset"


class ColumnCommand(StrEnum):
    """Everything the column context menu and the Columns menu can ask for.

    One enum for both, so the two entry points cannot drift apart — they used to
    disagree about whether ``expand`` meant this column or all of them.
    """

    NEW = "new"
    """A new card in this column."""

    CLEAR = "clear"
    """Delete every card in this column."""

    COLLAPSE = "collapse"
    EXPAND = "expand"
    """Un-collapse *this* column."""

    EXPAND_ALL = "expand_all"
    """Un-collapse every column."""

    RENAME = "rename"
    ADD_AFTER = "add_after"
    HIDE = "hide"
    DELETE = "delete"

    @property
    def needs_config(self) -> bool:
        """Whether this edits the saved board shape.

        A backend with no editable config (Jira's columns come from the status
        map) is offered the rest and not these.
        """
        return self in {
            ColumnCommand.RENAME,
            ColumnCommand.ADD_AFTER,
            ColumnCommand.HIDE,
            ColumnCommand.DELETE,
        }


class WorkItemCommand(StrEnum):
    """Actions offered by a work-item row's compact context menu."""

    VIEW = "view"
    EDIT = "edit"


class HelpTopic(StrEnum):
    KEYS = "keys"
    WHERE = "where"


class Action(BaseModel):
    """A kind and its payload, parsed out of a widget id or a menu key.

    Frozen: an action is a message about something that already happened, and
    nothing downstream has any business editing it.
    """

    model_config = ConfigDict(frozen=True)

    kind: ActionKind
    value: str = ""

    @field_validator("value", mode="before")
    @classmethod
    def _as_plain_text(cls, raw: object) -> str:
        """Accept an enum member as a payload, and store its text.

        Callers pass ``SortKey.DUE`` rather than ``"due"``; keeping the member
        itself would make ``encode()`` depend on the enum's ``__str__``.
        """
        return "" if raw is None else str(raw)

    # ---- the wire format -----------------------------------------------

    @classmethod
    def of(cls, kind: ActionKind, value: object = "") -> Action:
        return cls(kind=kind, value=str(value) if value is not None else "")

    @classmethod
    def parse(cls, raw: str | Action) -> Action | None:
        """Read ``"kind:value"``. ``None`` when the kind is not one of ours."""
        if isinstance(raw, Action):
            return raw
        kind, separator, value = raw.partition(SEPARATOR)
        if not separator or kind not in set(ActionKind):
            return None
        return cls(kind=ActionKind(kind), value=value)

    def encode(self) -> str:
        return f"{self.kind.value}{SEPARATOR}{self.value}"

    def __str__(self) -> str:
        return self.encode()

    # ---- widget ids ------------------------------------------------------

    @property
    def chip_id(self) -> str:
        """The widget id for this action's chip, if it has one."""
        return f"{CHIP_PREFIX}{self.kind.value}-{self.value}"

    @classmethod
    def from_chip_id(cls, widget_id: str) -> Action | None:
        """Recover the action a chip stands for from its widget id.

        Longest kind first, so ``pick-state`` is not read as the kind ``pick``
        that does not exist.
        """
        if not widget_id.startswith(CHIP_PREFIX):
            return None
        body = widget_id.removeprefix(CHIP_PREFIX)
        for kind in sorted(ActionKind, key=lambda member: -len(member.value)):
            if body.startswith(f"{kind.value}-"):
                return cls(kind=kind, value=body[len(kind.value) + 1 :])
        return None

    # ---- typed payloads --------------------------------------------------

    def enum(self, options: type[_E]) -> _E | None:
        """Read the payload as a member of ``options``, or ``None``.

        Unknown values come back as ``None`` rather than raising: the payload
        may have arrived from a stale widget id or a hand-edited config, and
        neither is worth a traceback.
        """
        try:
            return options(self.value)
        except ValueError:
            return None

    @property
    def pair(self) -> tuple[str, str]:
        """A ``field=value`` payload, split. The value may be empty."""
        field, _, wanted = self.value.partition("=")
        return field, wanted

    @property
    def flag(self) -> bool:
        """A payload standing for on or off."""
        return self.value == "1"

    @property
    def number(self) -> int | None:
        """A numeric payload, or ``None`` when it is empty or not a number."""
        try:
            return int(self.value)
        except ValueError:
            return None
