"""The board's shape, as saved to disk.

The shape is configuration, not code. Columns are a list you can add to, rename,
reorder and delete — nothing here assumes four of them, or five. The defaults in
:mod:`pykantui.core.workflows` are only the starting point written out on first
run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from pykantui.config.paths import config_path, write_text_atomic
from pykantui.core.filters import CardFilter
from pykantui.i18n import Locale
from pykantui.models import Column, ColumnRole, Edges, MenuLevel

DEFAULT_THEME = "cyberpunk"


class ColumnConfig(BaseModel):
    column_id: int
    name: str
    position: int
    visible: bool = True
    collapsed: bool = False

    #: Jira statuses that land in this column. Several per column is normal.
    jira_statuses: list[str] = Field(default_factory=list)


class BoardConfig(BaseModel):
    """Columns, their order, what they mean, and how the board looks.

    Roles are column ids rather than positions, so reordering columns does not
    silently change which one means "done". A role may be ``None`` — a board
    with no finish column simply never stamps a finish date.
    """

    #: Assignment is validated, not just construction. Half of these are set
    #: from a command-line flag or a menu pick as a plain string, and coercing
    #: at the moment of assignment is what keeps ``edges`` an :class:`Edges`
    #: everywhere downstream instead of sometimes being the string "square".
    model_config = ConfigDict(validate_assignment=True)

    columns: list[ColumnConfig] = Field(default_factory=list)
    reset_column: int | None = None
    start_column: int | None = None
    finish_column: int | None = None

    #: Named filter combinations, offered in the Filter menu.
    saved_filters: dict[str, CardFilter] = Field(default_factory=dict)

    #: How much of the menu bar to show. Remembered so it is set once rather
    #: than every session.
    menu_level: MenuLevel = MenuLevel.COLLAPSED

    #: Any Textual theme name. Dark by default — a board is something you leave
    #: open next to a terminal, and the default light surface glares.
    theme: str = DEFAULT_THEME

    #: Interface language. ``auto`` follows the environment or operating
    #: system; provider content and local Markdown are never translated.
    locale: Locale = Locale.AUTO

    #: Pill edges everywhere, or straight ones. Purely visual; it swaps the
    #: border style on cards, columns, fields and dialogs together.
    edges: Edges = Edges.ROUND

    #: Where this was loaded from, and where save() writes back to. ``None``
    #: for a config built in memory, which then never touches the disk.
    _path: Path | None = PrivateAttr(default=None)

    # ---- tolerating a hand-edited file ---------------------------------
    #
    # config.json is meant to be edited by hand, so a value that is not one of
    # ours falls back to the default instead of raising. A typo in the file
    # must not be the reason the board will not open — the same reason an
    # unknown theme name only warns.

    @field_validator("edges", mode="before")
    @classmethod
    def _known_edges(cls, raw: object) -> object:
        return raw if raw in set(Edges) else Edges.ROUND

    @field_validator("menu_level", mode="before")
    @classmethod
    def _known_menu_level(cls, raw: object) -> object:
        return raw if raw in set(MenuLevel) else MenuLevel.COLLAPSED

    @field_validator("locale", mode="before")
    @classmethod
    def _known_locale(cls, raw: object) -> object:
        return raw if raw in set(Locale) else Locale.AUTO

    # ---- derived views -------------------------------------------------

    def model_post_init(self, context: Any, /) -> None:
        # Keep ``columns`` itself in visual order. Everything else relies on
        # that, including renumber(), which cannot re-sort by position without
        # undoing the very move that called it.
        self.columns.sort(key=lambda column: column.position)

    @property
    def path(self) -> Path | None:
        """Where this was loaded from, or ``None`` if it is in-memory only."""
        return self._path

    def ordered(self) -> list[ColumnConfig]:
        return list(self.columns)

    def to_columns(self) -> list[Column]:
        return [
            Column(
                column_id=column.column_id,
                name=column.name,
                position=index,
                visible=column.visible,
                collapsed=column.collapsed,
            )
            for index, column in enumerate(self.ordered())
        ]

    def jira_column_mapping(self) -> dict[str, int]:
        return {status: column.column_id for column in self.ordered() for status in column.jira_statuses}

    def column_names(self) -> dict[int, str]:
        return {column.column_id: column.name for column in self.columns}

    def find(self, column_id: int) -> ColumnConfig | None:
        return next((column for column in self.columns if column.column_id == column_id), None)

    def find_by_name(self, name: str) -> ColumnConfig | None:
        wanted = name.strip().casefold()
        return next((column for column in self.columns if column.name.casefold() == wanted), None)

    def resolve(self, reference: str) -> ColumnConfig | None:
        """Look a column up by id, by name, or by 1-based position."""
        if reference.isdigit():
            number = int(reference)
            by_id = self.find(number)
            if by_id is not None:
                return by_id
            order = self.ordered()
            if 1 <= number <= len(order):
                return order[number - 1]
            return None
        return self.find_by_name(reference)

    def next_column_id(self) -> int:
        return max((column.column_id for column in self.columns), default=0) + 1

    def first_column_id(self) -> int | None:
        order = self.ordered()
        return order[0].column_id if order else None

    # ---- mutation ------------------------------------------------------

    def add(
        self,
        name: str,
        *,
        after: ColumnConfig | None = None,
        statuses: list[str] | None = None,
        visible: bool = True,
    ) -> ColumnConfig:
        order = self.ordered()
        index = len(order) if after is None else order.index(after) + 1
        column = ColumnConfig(
            column_id=self.next_column_id(),
            name=name,
            position=index,
            visible=visible,
            jira_statuses=statuses or [],
        )
        order.insert(index, column)
        self.columns = order
        self.renumber()
        return column

    def remove(self, column: ColumnConfig) -> None:
        self.columns = [other for other in self.columns if other.column_id != column.column_id]
        for role in ColumnRole:
            if self.column_for(role) == column.column_id:
                self.set_role(role, None)
        self.renumber()

    def move(self, column: ColumnConfig, position: int) -> None:
        """Put ``column`` at 1-based ``position``, shifting the rest along."""
        order = [other for other in self.ordered() if other.column_id != column.column_id]
        index = max(0, min(position - 1, len(order)))
        order.insert(index, column)
        self.columns = order
        self.renumber()

    def renumber(self) -> None:
        """Make positions match list order — not the other way round."""
        for index, column in enumerate(self.columns):
            column.position = index

    # ---- roles ---------------------------------------------------------

    def column_for(self, role: ColumnRole) -> int | None:
        """Which column plays ``role``, if any."""
        column_id = getattr(self, role.field)
        return int(column_id) if column_id is not None else None

    def set_role(self, role: ColumnRole | str, column_id: int | None) -> None:
        """Point a role at a column, or at nothing.

        Accepts the role's name as a string so the CLI can pass one straight
        through; an unknown name raises rather than silently setting nothing.
        """
        setattr(self, ColumnRole(role).field, column_id)

    def role_of(self, column_id: int) -> ColumnRole | None:
        return next((role for role in ColumnRole if self.column_for(role) == column_id), None)

    # ---- persistence ---------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> BoardConfig:
        """Read the saved shape, writing the default out on first run."""
        target = path or config_path()
        if not target.exists():
            config = default_config()
            config.save(target)
            return config

        document: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        config = cls(**document)
        config._path = target
        return config

    def save(self, path: Path | None = None) -> None:
        """Write the shape back to where it came from.

        A config that was built in memory rather than loaded — a demo board, a
        test fixture — has nowhere of its own, and saving is a no-op. It must
        never fall back to the real config file: collapsing a column in a demo
        would otherwise rewrite the user's actual board.
        """
        target = path or self._path
        if target is None:
            return
        write_text_atomic(target, json.dumps(self.model_dump(mode="json"), indent=2))
        self._path = target


def default_config() -> BoardConfig:
    """The starting shape, from :mod:`pykantui.core.workflows`."""
    from pykantui.core import workflows  # noqa: PLC0415 - avoids an import cycle

    return BoardConfig(
        columns=[
            ColumnConfig(
                column_id=column.column_id,
                name=column.name,
                position=column.position,
                visible=column.visible,
                jira_statuses=sorted(
                    status for status, column_id in workflows.JIRA_STATUS_MAP.items() if column_id == column.column_id
                ),
            )
            for column in workflows.DEFAULT_COLUMNS
        ],
        reset_column=workflows.RESET_COLUMN,
        start_column=workflows.START_COLUMN,
        finish_column=workflows.FINISH_COLUMN,
    )
