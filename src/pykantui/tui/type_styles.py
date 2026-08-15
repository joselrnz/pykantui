"""Theme-aware presentation of provider-neutral work-item types.

Providers keep their native type names.  This module derives a small visual
semantic from the normalized display name, so Jira vocabulary is neither the
schema nor a special case and third-party providers receive a safe fallback.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from enum import StrEnum

from rich.style import Style
from rich.text import Text
from textual.color import Color, ColorParseError


class ItemTypeSemantic(StrEnum):
    """Provider-neutral meaning inferred from a native item-type label."""

    DEFECT = "defect"
    EPIC = "epic"
    FEATURE = "feature"
    STORY = "story"
    SUBTASK = "subtask"
    TASK = "task"
    NEUTRAL = "neutral"


class TypeThemeVariable(StrEnum):
    """Base Textual theme tokens used for item-type semantics."""

    ERROR = "error"
    WARNING = "warning"
    SUCCESS = "success"
    PRIMARY = "primary"
    ACCENT = "accent"
    SECONDARY = "secondary"
    FOREGROUND = "foreground"

    @property
    def token(self) -> str:
        """Return the TCSS spelling of this variable."""
        return f"${self.value}"


_TYPE_VARIABLES: Mapping[ItemTypeSemantic, TypeThemeVariable] = {
    ItemTypeSemantic.DEFECT: TypeThemeVariable.ERROR,
    ItemTypeSemantic.EPIC: TypeThemeVariable.WARNING,
    ItemTypeSemantic.FEATURE: TypeThemeVariable.SUCCESS,
    ItemTypeSemantic.STORY: TypeThemeVariable.PRIMARY,
    ItemTypeSemantic.SUBTASK: TypeThemeVariable.ACCENT,
    ItemTypeSemantic.TASK: TypeThemeVariable.SECONDARY,
    ItemTypeSemantic.NEUTRAL: TypeThemeVariable.FOREGROUND,
}

# Match whole normalized words or phrases.  The order is deliberate: a
# "child task" is a subtask, not a generic task, and an "epic story" keeps the
# higher-level epic semantic.
_TYPE_ALIASES: tuple[tuple[ItemTypeSemantic, frozenset[str]], ...] = (
    (
        ItemTypeSemantic.SUBTASK,
        frozenset({"subtask", "sub task", "child", "child task", "child issue"}),
    ),
    (
        ItemTypeSemantic.DEFECT,
        frozenset({"bug", "defect", "incident", "problem", "regression", "vulnerability"}),
    ),
    (
        ItemTypeSemantic.EPIC,
        frozenset({"epic", "initiative", "theme", "milestone", "objective"}),
    ),
    (ItemTypeSemantic.STORY, frozenset({"story", "user story"})),
    (
        ItemTypeSemantic.FEATURE,
        frozenset({"feature", "enhancement", "improvement", "request", "change", "idea"}),
    ),
    (
        ItemTypeSemantic.TASK,
        frozenset({"task", "chore", "work item", "issue", "ticket", "action item", "spike", "research"}),
    ),
)

WORK_ITEM_TYPE_CLASSES: frozenset[str] = frozenset(
    f"work-item-type-{variable.value}" for variable in TypeThemeVariable
)


def normalize_item_type(value: object) -> str:
    """Normalize a native item-type display name for semantic matching.

    Compatibility normalization handles full-width Latin text, while replacing
    punctuation with spaces makes provider spellings such as ``sub-task`` and
    ``sub_task`` equivalent.  Non-text API values are deliberately ignored.
    """
    if not isinstance(value, str):
        return ""
    compatible = unicodedata.normalize("NFKC", value).casefold()
    words = "".join(character if character.isalnum() else " " for character in compatible)
    return " ".join(words.split())


def item_type_semantic(value: object) -> ItemTypeSemantic:
    """Infer a visual semantic from a provider's normalized type name."""
    normalized = normalize_item_type(value)
    if not normalized:
        return ItemTypeSemantic.NEUTRAL
    padded = f" {normalized} "
    for semantic, aliases in _TYPE_ALIASES:
        if any(normalized == alias or f" {alias} " in padded for alias in aliases):
            return semantic
    return ItemTypeSemantic.NEUTRAL


def type_theme_variable(value: object) -> TypeThemeVariable:
    """Return the active-theme token associated with a native type name."""
    return _TYPE_VARIABLES[item_type_semantic(value)]


def work_item_type_class(value: object) -> str:
    """Return the TCSS class corresponding to a native type name."""
    return f"work-item-type-{type_theme_variable(value).value}"


def resolve_type_color(value: object, theme_variables: Mapping[str, str]) -> Color:
    """Resolve an item type against active Textual theme variables safely."""
    variable = type_theme_variable(value)
    candidates = (
        theme_variables.get(variable.value),
        theme_variables.get(TypeThemeVariable.FOREGROUND.value),
        "ansi_default",
    )
    for raw in candidates:
        if not raw:
            continue
        try:
            return Color.parse(raw)
        except ColorParseError:
            continue
    return Color.parse("ansi_default")  # pragma: no cover - literal is valid


def work_item_type_text(
    label: str,
    value: object,
    theme_variables: Mapping[str, str],
) -> Text:
    """Build a single-line Rich type cell that truncates without wrapping."""
    color = resolve_type_color(value, theme_variables)
    single_line_label = " ".join(label.split())
    return Text(
        single_line_label,
        style=Style(color=color.rich_color),
        overflow="ellipsis",
        no_wrap=True,
    )


__all__ = [
    "WORK_ITEM_TYPE_CLASSES",
    "ItemTypeSemantic",
    "TypeThemeVariable",
    "item_type_semantic",
    "normalize_item_type",
    "resolve_type_color",
    "type_theme_variable",
    "work_item_type_class",
    "work_item_type_text",
]
