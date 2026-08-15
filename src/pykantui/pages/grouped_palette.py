"""A searchable command palette with collapsible, provider-aware groups."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.fuzzy import FuzzySearch
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from pykantui.i18n import translate as _
from pykantui.tui.glyphs import SEARCH_GLYPH


@dataclass(frozen=True)
class PaletteCommand:
    """One executable leaf in the palette tree."""

    command_id: str
    label: str
    description: str
    callback: Callable[[], object]


@dataclass(frozen=True)
class PaletteGroup:
    """A collapsible group containing commands or more groups."""

    group_id: str
    label: str
    description: str
    children: tuple[PaletteNode, ...]


PaletteNode: TypeAlias = PaletteCommand | PaletteGroup


@dataclass(frozen=True)
class _VisibleNode:
    node: PaletteNode
    depth: int
    ancestors: tuple[PaletteGroup, ...]
    display_label: str


class GroupedCommandPalette(ModalScreen[PaletteCommand | None]):
    """Keep Textual's wide palette interaction while grouping discovery rows."""

    AUTO_FOCUS = "#grouped-palette-search"
    BINDINGS = [
        Binding("down", "cursor_down", "Next command", show=False, priority=True),
        Binding("up", "cursor_up", "Previous command", show=False, priority=True),
        Binding("enter", "select", "Open or run", show=False, priority=True),
        Binding("right", "expand", "Expand group", show=False, priority=True),
        Binding("left", "collapse", "Collapse group", show=False, priority=True),
        Binding("escape", "cancel", "Close", show=False, priority=True),
    ]
    CSS = """
    GroupedCommandPalette {
        background: $background 60%;
        align-horizontal: center;
    }

    GroupedCommandPalette #grouped-palette-container {
        width: 100%;
        height: 100%;
        margin-top: 3;
        background: $surface;
    }

    GroupedCommandPalette #grouped-palette-input-row {
        width: 100%;
        height: 3;
        border: none;
        border-bottom: solid $primary;
        background: transparent;
    }

    GroupedCommandPalette #grouped-palette-search-icon {
        width: 3;
        height: 1;
        margin-top: 0;
        color: $accent;
        text-style: bold;
        content-align: center middle;
    }

    GroupedCommandPalette #grouped-palette-search,
    GroupedCommandPalette #grouped-palette-search:focus {
        width: 1fr;
        height: 3;
        padding: 0;
        border: none;
        background: transparent;
        color: $foreground;
    }

    GroupedCommandPalette #grouped-palette-options {
        width: 100%;
        height: auto;
        max-height: 70vh;
        padding: 0;
        border: none;
        background: transparent;
    }

    GroupedCommandPalette #grouped-palette-options > .option-list--option {
        padding: 0 2;
        color: $foreground;
    }

    GroupedCommandPalette #grouped-palette-options > .option-list--option-highlighted {
        color: $block-cursor-foreground;
        background: $block-cursor-background;
        text-style: $block-cursor-text-style;
    }
    """

    def __init__(self, nodes: tuple[PaletteNode, ...]) -> None:
        super().__init__(id="--grouped-command-palette")
        self.nodes = nodes
        self._expanded: set[str] = set()
        self._visible: list[_VisibleNode] = []

    @property
    def visible_labels(self) -> tuple[str, ...]:
        """Labels currently presented to the user, without styling glyphs."""
        return tuple(item.display_label for item in self._visible)

    def compose(self) -> ComposeResult:
        with Vertical(id="grouped-palette-container"):
            with Horizontal(id="grouped-palette-input-row"):
                yield Static(SEARCH_GLYPH, id="grouped-palette-search-icon")
                yield Input(
                    placeholder=_("Search commands or groups…"),
                    select_on_focus=False,
                    id="grouped-palette-search",
                )
            yield OptionList(id="grouped-palette-options")

    def on_mount(self) -> None:
        self._refresh_options()

    @on(Input.Changed, "#grouped-palette-search")
    def search_changed(self, event: Input.Changed) -> None:
        self._refresh_options(event.value)

    @on(OptionList.OptionSelected, "#grouped-palette-options")
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        self._activate(event.option_index)

    def action_cursor_down(self) -> None:
        self.query_one("#grouped-palette-options", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#grouped-palette-options", OptionList).action_cursor_up()

    def action_select(self) -> None:
        options = self.query_one("#grouped-palette-options", OptionList)
        if options.highlighted is not None:
            self._activate(options.highlighted)

    def action_expand(self) -> None:
        search = self.query_one("#grouped-palette-search", Input)
        if search.value:
            search.action_cursor_right()
            return
        selected = self._selected()
        if selected is None or not isinstance(selected.node, PaletteGroup):
            return
        if selected.node.group_id in self._expanded:
            self.action_cursor_down()
            return
        self._expanded.add(selected.node.group_id)
        self._refresh_options(select_id=selected.node.group_id)

    def action_collapse(self) -> None:
        search = self.query_one("#grouped-palette-search", Input)
        if search.value:
            search.action_cursor_left()
            return
        selected = self._selected()
        if selected is None:
            return
        if isinstance(selected.node, PaletteGroup) and selected.node.group_id in self._expanded:
            group = selected.node
        elif selected.ancestors:
            group = selected.ancestors[-1]
        else:
            return
        self._expanded.discard(group.group_id)
        self._refresh_options(select_id=group.group_id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected(self) -> _VisibleNode | None:
        highlighted = self.query_one("#grouped-palette-options", OptionList).highlighted
        if highlighted is None or highlighted >= len(self._visible):
            return None
        return self._visible[highlighted]

    def _activate(self, index: int) -> None:
        if index >= len(self._visible):
            return
        item = self._visible[index]
        if isinstance(item.node, PaletteCommand):
            self.dismiss(item.node)
            return
        group_id = item.node.group_id
        if group_id in self._expanded:
            self._expanded.remove(group_id)
        else:
            self._expanded.add(group_id)
        self._refresh_options(select_id=group_id)

    def _refresh_options(self, query: str | None = None, *, select_id: str | None = None) -> None:
        if query is None:
            query = self.query_one("#grouped-palette-search", Input).value
        self._visible = self._search(query) if query.strip() else self._discover()
        options = self.query_one("#grouped-palette-options", OptionList)
        options.clear_options()
        if not self._visible:
            options.add_option(Option(Text("No matches found", justify="center"), disabled=True))
            return
        options.add_options([Option(self._prompt(item)) for item in self._visible])
        target = 0
        if select_id is not None:
            target = next(
                (
                    index
                    for index, item in enumerate(self._visible)
                    if isinstance(item.node, PaletteGroup) and item.node.group_id == select_id
                ),
                0,
            )
        options.highlighted = target

    def _discover(self) -> list[_VisibleNode]:
        visible: list[_VisibleNode] = []

        def visit(nodes: tuple[PaletteNode, ...], depth: int, ancestors: tuple[PaletteGroup, ...]) -> None:
            for node in nodes:
                visible.append(_VisibleNode(node, depth, ancestors, node.label))
                if isinstance(node, PaletteGroup) and node.group_id in self._expanded:
                    visit(node.children, depth + 1, (*ancestors, node))

        visit(self.nodes, 0, ())
        return visible

    def _search(self, query: str) -> list[_VisibleNode]:
        needle = query.strip()
        matcher = FuzzySearch()
        matches: list[tuple[float, _VisibleNode]] = []

        def visit(nodes: tuple[PaletteNode, ...], ancestors: tuple[PaletteGroup, ...]) -> None:
            for node in nodes:
                if isinstance(node, PaletteGroup):
                    visit(node.children, (*ancestors, node))
                    continue
                path = (*[group.label for group in ancestors], node.label)
                haystack = " ".join((*path, node.description))
                score, _ = matcher.match(needle, haystack)
                if score:
                    matches.append((score, _VisibleNode(node, 0, ancestors, " · ".join(path))))

        visit(self.nodes, ())
        return [item for _, item in sorted(matches, key=lambda match: match[0], reverse=True)]

    def _prompt(self, item: _VisibleNode) -> Text:
        indent = "    " * item.depth
        prompt = Text()
        if isinstance(item.node, PaletteGroup):
            marker = "▾" if item.node.group_id in self._expanded else "▸"
            prompt.append(f"{indent}{marker} {item.node.label}", style="bold #00C8FF")
        else:
            prompt.append(f"{indent}{item.display_label}", style="bold")
        prompt.append(f"\n{indent}{item.node.description}", style="#7D8590")
        return prompt
