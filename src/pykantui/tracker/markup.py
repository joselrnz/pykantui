"""Turning three different body formats into markdown.

The providers disagree completely about how an issue body is carried:

* **Jira** returns Atlassian Document Format on REST v3 -- a nested JSON tree --
  and wiki markup on v2. Which one arrives depends on the endpoint, so the
  entry point here sniffs rather than being told.
* **Plane** returns ``description_html``.
* **Trello** returns markdown already, and needs nothing done to it.

:func:`to_markdown` is the only function callers need; it works out which of
these it has been handed.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

__all__ = ["to_markdown", "adf_to_markdown", "html_to_markdown", "wiki_to_markdown"]


def to_markdown(value: Any) -> str:
    """Convert an issue body of unknown format into markdown.

    Sniffing rather than trusting a declared format is deliberate: the same
    Jira site serves ADF or wiki markup for the same field depending on which
    API version the call went through, and a mis-declared format writes
    ``{'type': 'doc', 'version': 1, ...}`` into a file a human is meant to read.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        return adf_to_markdown(value)
    if not isinstance(value, str):
        return str(value).strip()

    text = value.strip()
    if not text:
        return ""
    if _looks_like_html(text):
        return html_to_markdown(text)
    if _looks_like_wiki(text):
        return wiki_to_markdown(text)
    return text


# ---- Atlassian Document Format ------------------------------------------


def adf_to_markdown(node: Any, depth: int = 0) -> str:
    """Flatten an ADF document into markdown.

    Handles the node types an issue description actually uses. Anything
    unrecognised falls through to its children rather than being dropped, so an
    exotic node costs its formatting but never its text.
    """
    if not isinstance(node, dict):
        return ""

    kind = node.get("type", "")
    content = node.get("content", []) or []

    if kind == "text":
        return _adf_marks(str(node.get("text", "")), node.get("marks", []) or [])
    if kind == "hardBreak":
        return "  \n"
    if kind == "rule":
        return "\n---\n"
    if kind == "doc":
        return _join_blocks(adf_to_markdown(child, depth) for child in content)
    if kind == "paragraph":
        return _inline(content, depth)
    if kind == "heading":
        level = int(node.get("attrs", {}).get("level", 1))
        return f"{'#' * max(1, min(level, 6))} {_inline(content, depth)}"
    if kind == "codeBlock":
        language = str(node.get("attrs", {}).get("language", "") or "")
        return f"```{language}\n{_inline(content, depth)}\n```"
    if kind == "blockquote":
        inner = _join_blocks(adf_to_markdown(child, depth) for child in content)
        return "\n".join(f"> {line}" if line else ">" for line in inner.splitlines())
    if kind in ("bulletList", "orderedList"):
        return _adf_list(node, kind, depth)
    if kind == "listItem":
        return _join_blocks(adf_to_markdown(child, depth) for child in content)
    if kind == "panel":
        inner = _join_blocks(adf_to_markdown(child, depth) for child in content)
        flavour = str(node.get("attrs", {}).get("panelType", "info"))
        return f"> **{flavour}**\n" + "\n".join(f"> {line}" for line in inner.splitlines())
    if kind in ("mediaSingle", "mediaGroup", "media"):
        return _adf_media(node, content, depth)
    if kind == "mention":
        return f"@{node.get('attrs', {}).get('text', '').lstrip('@')}"
    if kind == "emoji":
        attrs = node.get("attrs", {})
        return str(attrs.get("text") or attrs.get("shortName") or "")
    if kind == "inlineCard":
        return str(node.get("attrs", {}).get("url", ""))
    if kind == "table":
        return _adf_table(content, depth)

    return _join_blocks(adf_to_markdown(child, depth) for child in content)


def _adf_marks(text: str, marks: list[dict[str, Any]]) -> str:
    """Apply ADF inline marks, innermost first."""
    for mark in marks:
        kind = mark.get("type", "")
        if kind == "strong":
            text = f"**{text}**"
        elif kind == "em":
            text = f"*{text}*"
        elif kind == "code":
            text = f"`{text}`"
        elif kind == "strike":
            text = f"~~{text}~~"
        elif kind == "link":
            href = mark.get("attrs", {}).get("href", "")
            text = f"[{text}]({href})" if href else text
    return text


def _adf_list(node: dict[str, Any], kind: str, depth: int) -> str:
    items = node.get("content", []) or []
    pad = "  " * depth
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        bullet = f"{index}." if kind == "orderedList" else "-"
        body = adf_to_markdown(item, depth + 1).strip()
        if not body:
            continue
        first, *rest = body.splitlines()
        lines.append(f"{pad}{bullet} {first}")
        # Continuation lines line up under the text, not the bullet.
        lines.extend(f"{pad}  {line}" for line in rest)
    return "\n".join(lines)


def _adf_media(node: dict[str, Any], content: list[Any], depth: int) -> str:
    attrs = node.get("attrs", {})
    url = attrs.get("url") or attrs.get("id") or ""
    alt = attrs.get("alt", "attachment")
    if url:
        return f"![{alt}]({url})"
    return _join_blocks(adf_to_markdown(child, depth) for child in content)


def _adf_table(rows: list[Any], depth: int) -> str:
    """Render an ADF table, emitting the header separator after the first row."""
    lines: list[str] = []
    for index, row in enumerate(rows):
        cells = row.get("content", []) or []
        rendered = [adf_to_markdown(cell, depth).strip().replace("\n", " ") for cell in cells]
        lines.append("| " + " | ".join(rendered) + " |")
        if index == 0:
            lines.append("|" + "|".join(" --- " for _ in rendered) + "|")
    return "\n".join(lines)


def _inline(content: list[Any], depth: int) -> str:
    return "".join(adf_to_markdown(child, depth) for child in content)


def _join_blocks(parts: Any) -> str:
    return "\n\n".join(part for part in (p.strip("\n") for p in parts) if part.strip())


# ---- HTML ----------------------------------------------------------------


class _HtmlToMarkdown(HTMLParser):
    """A small HTML-to-markdown translator for issue bodies.

    Scoped to what a rich-text editor emits -- Plane's is TipTap -- rather than
    trying to be a general converter. Unknown tags contribute their text.
    """

    _INLINE = {
        "strong": "**",
        "b": "**",
        "em": "*",
        "i": "*",
        "code": "`",
        "del": "~~",
        "s": "~~",
        "strike": "~~",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._list_stack: list[tuple[str, int]] = []
        self._link: str = ""
        self._in_pre = False

    # -- blocks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: (value or "") for key, value in attrs}

        if tag in self._INLINE:
            self.parts.append(self._INLINE[tag])
        elif tag == "p":
            self._block()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._block()
            self.parts.append("#" * int(tag[1]) + " ")
        elif tag == "br":
            self.parts.append("  \n")
        elif tag == "hr":
            self._block()
            self.parts.append("---")
            self._block()
        elif tag in ("ul", "ol"):
            self._list_stack.append((tag, 0))
            self._block()
        elif tag == "li":
            self._li()
        elif tag == "a":
            self._link = attributes.get("href", "")
            self.parts.append("[")
        elif tag == "img":
            source = attributes.get("src", "")
            self.parts.append(f"![{attributes.get('alt', '')}]({source})")
        elif tag == "blockquote":
            self._block()
            self.parts.append("> ")
        elif tag == "pre":
            self._block()
            self.parts.append("```\n")
            self._in_pre = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._INLINE:
            self.parts.append(self._INLINE[tag])
        elif tag in ("p", "blockquote") or tag.startswith("h") and tag[1:].isdigit():
            self._block()
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._block()
        elif tag == "a":
            self.parts.append(f"]({self._link})" if self._link else "]")
            self._link = ""
        elif tag == "pre":
            self._in_pre = False
            self.parts.append("\n```")
            self._block()

    def handle_data(self, data: str) -> None:
        if self._in_pre:
            self.parts.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        if text.strip() or (self.parts and not self.parts[-1].endswith(("\n", " "))):
            self.parts.append(text)

    def _li(self) -> None:
        if not self._list_stack:
            self._list_stack.append(("ul", 0))
        tag, count = self._list_stack[-1]
        count += 1
        self._list_stack[-1] = (tag, count)
        indent = "  " * (len(self._list_stack) - 1)
        bullet = f"{count}." if tag == "ol" else "-"
        self.parts.append(f"\n{indent}{bullet} ")

    def _block(self) -> None:
        if self.parts and not "".join(self.parts[-2:]).endswith("\n\n"):
            self.parts.append("\n\n")

    def result(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(html: str) -> str:
    """Convert an HTML issue body to markdown."""
    parser = _HtmlToMarkdown()
    parser.feed(html)
    parser.close()
    return parser.result()


# ---- Jira wiki markup ----------------------------------------------------

_WIKI_HEADING = re.compile(r"^h([1-6])\.\s*", re.MULTILINE)
_WIKI_CODE = re.compile(r"\{code(?::([^}]*))?\}(.*?)\{code\}", re.DOTALL)
_WIKI_NOFORMAT = re.compile(r"\{noformat\}(.*?)\{noformat\}", re.DOTALL)
_WIKI_QUOTE = re.compile(r"\{quote\}(.*?)\{quote\}", re.DOTALL)
_WIKI_MONO = re.compile(r"\{\{(.+?)\}\}")
_WIKI_BOLD = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_WIKI_ITALIC = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")
_WIKI_STRIKE = re.compile(r"(?<![\w-])-([^-\n]+)-(?![\w-])")
_WIKI_NAMED_LINK = re.compile(r"\[([^|\]]+)\|([^\]]+)\]")
_WIKI_BARE_LINK = re.compile(r"\[([^\]|]+)\]")
_WIKI_LIST = re.compile(r"^([*#]+)\s+", re.MULTILINE)


def wiki_to_markdown(text: str) -> str:
    """Convert Jira wiki markup to markdown.

    Code blocks come out first and go back in last, so that the inline rules --
    which would happily mangle an asterisk inside a shell snippet -- never see
    their contents.
    """
    vault: list[str] = []

    def stash(replacement: str) -> str:
        vault.append(replacement)
        return f"\x00{len(vault) - 1}\x00"

    text = _WIKI_CODE.sub(lambda m: stash(f"```{m.group(1) or ''}\n{m.group(2).strip()}\n```"), text)
    text = _WIKI_NOFORMAT.sub(lambda m: stash(f"```\n{m.group(1).strip()}\n```"), text)
    text = _WIKI_MONO.sub(lambda m: stash(f"`{m.group(1)}`"), text)

    # Lists go first, and specifically *before* headings. In wiki markup a
    # leading "#" is an ordered-list bullet, so the list rule cannot tell one
    # from the "#" that the heading rule emits -- run headings first and
    # "h1. Title" becomes "# Title" becomes "1. Title".
    text = _WIKI_LIST.sub(lambda m: "  " * (len(m.group(1)) - 1) + ("1. " if m.group(1)[-1] == "#" else "- "), text)
    text = _WIKI_HEADING.sub(lambda m: "#" * int(m.group(1)) + " ", text)
    text = _WIKI_QUOTE.sub(lambda m: "\n".join(f"> {line}" for line in m.group(1).strip().splitlines()), text)
    text = _WIKI_NAMED_LINK.sub(r"[\1](\2)", text)
    text = _WIKI_BARE_LINK.sub(r"<\1>", text)
    text = _WIKI_BOLD.sub(r"**\1**", text)
    text = _WIKI_ITALIC.sub(r"*\1*", text)
    text = _WIKI_STRIKE.sub(r"~~\1~~", text)

    for index, original in enumerate(vault):
        text = text.replace(f"\x00{index}\x00", original)
    return text.strip()


# ---- sniffing ------------------------------------------------------------

_HTML_TAG = re.compile(r"<(p|div|br|ul|ol|li|h[1-6]|strong|em|b|i|a|code|pre|img|blockquote|span|table)\b[^>]*>", re.I)
_WIKI_MARKER = re.compile(r"(^h[1-6]\.\s)|(\{code)|(\{noformat)|(\{quote)|(\{\{)|(^[*#]+\s)|(\[[^\]]+\|[^\]]+\])", re.M)


def _looks_like_html(text: str) -> bool:
    return bool(_HTML_TAG.search(text))


def _looks_like_wiki(text: str) -> bool:
    return bool(_WIKI_MARKER.search(text))


def strip_html(text: str) -> str:
    """Plain text from HTML, for a summary line or a search index."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()
