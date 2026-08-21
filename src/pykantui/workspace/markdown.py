"""One issue as one markdown file, and back again.

The file has three parts, and the split is the whole reason two-way sync is
safe::

    ---
    key: JPT-4
    title: Task 1
    ...
    ---

    <!-- pykantui:source -->
    the description as the tracker has it

    <!-- pykantui:notes -->
    whatever you wrote

Everything above ``pykantui:notes`` is **rewritten from the tracker on every
sync**. Everything below it is **never touched**. Without that line, the second
sync silently eats whatever you typed -- which is the failure that makes people
stop trusting a tool like this.

Frontmatter goes through PyYAML rather than a hand-rolled parser. These files
are edited by a human in an ordinary editor, and a tolerant reader that
silently mis-parses ``title: Fix: the thing`` is worse than one that raises.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from html import escape, unescape
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from pykantui.tracker.models import CommentDraft, IssueEdit, RemoteComment, RemoteIssue

#: Marks the start of the tracker-owned body. Everything between this and the
#: notes marker is regenerated on every sync.
SOURCE_MARKER = "<!-- pykantui:source — from the tracker, rewritten on every sync -->"

#: Marks the start of the user-owned body. Nothing below this is ever written
#: by a sync.
NOTES_MARKER = "<!-- pykantui:notes — yours, never touched by a sync -->"

#: Provider-owned discussion records. This region is opt-in so opening an old
#: workspace and finding no comments does not churn every card in local Git.
COMMENTS_MARKER = "<!-- pykantui:comments — from the provider, rewritten on refresh -->"

#: Locally authored append-only comments. They remain here until a confirmed
#: sync receives a provider id and atomically rewrites the card.
COMMENT_DRAFTS_MARKER = (
    "<!-- pykantui:comment-drafts — yours until a confirmed sync sends them -->"
)

#: Local-only agent metadata: cross-card dependencies and delegated
#: ownership, for MCP-driven workflows. Never read from or written to a
#: provider -- exactly like ``pykantui:notes``. Always attribute-bearing, so
#: (like ``pykantui:comment``) there is no static marker text, only the regex
#: below and the ``format_agent_block``/``parse_agent_block`` helpers.

#: Markers are *matched* on their token alone, so the human-readable text after
#: it can be reworded -- or be missing entirely, in a file written by an older
#: version -- without orphaning somebody's notes.
_SOURCE_RE = re.compile(r"<!--\s*pykantui:source[^>]*-->")
_NOTES_RE = re.compile(r"<!--\s*pykantui:notes[^>]*-->")
_COMMENTS_RE = re.compile(r"(?m)^\s*<!--\s*pykantui:comments\b[^>]*-->\s*$")
_COMMENT_DRAFTS_RE = re.compile(r"(?m)^\s*<!--\s*pykantui:comment-drafts\b[^>]*-->\s*$")
_COMMENT_START_RE = re.compile(r"(?m)^\s*<!--\s*pykantui:comment\s+([^>]*)-->\s*$")
_COMMENT_DRAFT_START_RE = re.compile(r"(?m)^\s*<!--\s*pykantui:comment-draft\s+([^>]*)-->\s*$")
_AGENT_RE = re.compile(r"(?m)^\s*<!--\s*pykantui:agent\s*([^>]*?)\s*-->\s*$")
_ATTRIBUTE_RE = re.compile(r'([a-z][a-z0-9-]*)="([^"]*)"')
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _without_heading(text: str) -> str:
    """Drop the generated ``# KEY · Title`` line.

    The heading is ours, not the tracker's. Left in the body it would make
    every file look edited the first time it was compared, and the first sync
    would try to push the heading back as part of the description.
    """
    stripped = text.lstrip("\n")
    if stripped.startswith("# "):
        _, _, rest = stripped.partition("\n")
        return rest
    return text


_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

#: Frontmatter keys a user may edit and have pushed back. Anything else in the
#: block is informational -- rewritten each sync and ignored on read -- so that
#: editing ``status:`` by hand does not quietly contradict the folder the file
#: is sitting in. Documented in ``docs/markdown-format.md``.
EDITABLE_KEYS = ("title", "assignee", "labels", "components", "due", "priority", "type")


class EditableFrontmatter(BaseModel):
    """The human-editable subset of a card's YAML frontmatter."""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    assignee: str | None = None
    labels: list[str] | None = None
    components: list[str] | None = None
    due: date | None = None
    due_date: date | None = None
    priority: str | None = None
    type: str | None = None

    @field_validator("title", "assignee", "priority", "type", mode="before")
    @classmethod
    def _text_scalar(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, (bool, dict, list, tuple)):
            raise ValueError("must be text")
        return str(value)

    @field_validator("labels", "components", mode="before")
    @classmethod
    def _text_list(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            raise ValueError("must be a YAML list, for example [bug, backend]")
        if any(isinstance(item, (dict, list, tuple)) for item in value):
            raise ValueError("must contain only text values")
        return [str(item) for item in value]


class IssueFile:
    """A parsed issue file and its provider/local discussion regions."""

    def __init__(
        self,
        front: dict[str, Any],
        source: str,
        notes: str,
        *,
        comments: tuple[RemoteComment, ...] = (),
        comment_drafts: tuple[CommentDraft, ...] = (),
        has_comment_region: bool = False,
        agent_block: str = "",
        errors: tuple[str, ...] = (),
    ) -> None:
        self.front = front
        self.source = source
        self.notes = notes
        self.comments = comments
        self.comment_drafts = comment_drafts
        self.has_comment_region = has_comment_region
        self.agent_block = agent_block
        self.errors = errors

    @property
    def valid(self) -> bool:
        """Whether editable metadata is safe to translate into provider writes."""

        return not self.errors

    @property
    def key(self) -> str:
        return str(self.front.get("key", "") or "")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"IssueFile(key={self.key!r}, notes={len(self.notes)} chars)"


def render(
    issue: RemoteIssue,
    *,
    column_name: str,
    notes: str = "",
    provider: str = "",
    comments: tuple[RemoteComment, ...] = (),
    comment_drafts: tuple[CommentDraft, ...] = (),
    include_comment_region: bool = False,
    agent_block: str = "",
) -> str:
    """Build the file for one issue, preserving ``notes`` underneath it.

    Conforms to ``docs/markdown-format.md``. Field order is fixed there --
    identity, state, people, dates, links -- so files diff cleanly and a reader
    learns where to look.
    """
    front: dict[str, Any] = {}

    def put(name: str, value: Any) -> None:
        """Add a field, unless it is empty.

        Omitting empties is the rule from the spec: a block full of bare keys
        is noise, and it makes "no priority" indistinguishable from "this
        tracker has no such concept".
        """
        if value not in (None, "", (), []):
            front[name] = value

    # identity
    #
    # The real key only. ``display_key()`` falls back to the id, which for a
    # draft is a local string like ``draft-port-the-picker`` -- writing that
    # under ``key:`` would make an unsent story look like it had one.
    put("key", issue.key)
    # Always a string, always quoted on the way out: an id is opaque, and
    # YAML would read 007 as the integer 7.
    front["id"] = _Quoted(issue.issue_id)
    put("provider", provider)

    # state
    #
    # Collapsed here as well as on the model. `model_copy(update=...)` skips
    # validators, so a caller that builds an issue that way can carry a
    # newline in its title -- and a newline in a YAML scalar turns the whole
    # block into something no longer matching the spec. A format guarantee has
    # to be enforced by the writer, not assumed from upstream.
    put("title", " ".join(issue.title.split()) if issue.title else "")
    put("status", issue.status)  # what the tracker calls it
    put("column", column_name)  # the folder it is in
    put("type", issue.issue_type)
    put("priority", issue.priority)

    # people and relationships
    put("assignee", issue.assignee)
    put("reporter", issue.reporter)
    put("labels", list(issue.labels))
    put("components", list(issue.components))
    put("parent", issue.parent_key)

    # dates, as ISO 8601 strings rather than YAML timestamps
    put("created", _iso(issue.created_at))
    put("updated", _iso(issue.updated_at))
    put("due", issue.due_date.isoformat() if issue.due_date else "")

    put("url", issue.url)

    block = yaml.dump(
        front,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,  # never wrap a URL or a long title across lines
    ).rstrip()

    title = str(front.get("title", ""))
    # Key and title, whichever of them exist. A draft has no key and a
    # titleless issue has no title; neither should leave a dangling separator.
    heading = ("# " + " · ".join(part for part in (issue.key, title) if part)).rstrip()
    parts = [f"---\n{block}\n---", "", heading, "", SOURCE_MARKER]

    body = issue.body.strip()
    if body:
        parts += ["", body]
    if comments or comment_drafts or include_comment_region:
        parts += ["", COMMENTS_MARKER]
        for comment in comments:
            parts += ["", *_render_remote_comment(comment)]
        parts += ["", COMMENT_DRAFTS_MARKER]
        for draft in comment_drafts:
            parts += ["", *_render_comment_draft(draft)]
    if agent_block:
        parts += ["", f"<!-- pykantui:agent {agent_block} -->"]
    parts += ["", NOTES_MARKER]

    tail = notes.strip()
    if tail:
        parts += ["", tail]
    return "\n".join(parts).rstrip() + "\n"


class _Quoted(str):
    """A string that must survive the round trip as a string.

    Ids like ``007`` or ``10018`` would otherwise be re-read as integers, and
    an id that changes type between write and read is an id that stops matching.
    """


class _Dumper(yaml.SafeDumper):
    """Emits the spec's types: quoted ids, flow-style label lists."""


def _quoted_representer(dumper: yaml.SafeDumper, value: _Quoted) -> Any:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style='"')


def _list_representer(dumper: yaml.SafeDumper, value: list[Any]) -> Any:
    # Flow style: `labels: [a, b]` is one line and one diff hunk.
    return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=True)


_Dumper.add_representer(_Quoted, _quoted_representer)
_Dumper.add_representer(list, _list_representer)


def _iso(value: datetime | None) -> str:
    """An ISO 8601 timestamp, seconds precision.

    PyYAML's native timestamp type writes ``2026-08-07 20:56:05.516000-05:00``
    -- a space where ISO 8601 wants a ``T``, plus microseconds nobody asked
    for. Valid YAML, but not what any other tool reading these files expects.
    """
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def parse(text: str) -> IssueFile:
    """Split a file into frontmatter, tracker body and user notes.

    Tolerant on purpose: a file with no frontmatter, or with the markers
    missing because someone deleted them, still parses. Everything before the
    notes marker is treated as tracker-owned and everything after as yours; a
    file with no notes marker at all is read as having no notes rather than
    having its whole body treated as notes, because the alternative would
    freeze the body against future syncs.
    """
    front: dict[str, Any] = {}
    body = text
    errors: list[str] = []

    match = _FRONTMATTER.match(text)
    if match:
        body = text[match.end() :]
        try:
            loaded = yaml.load(match.group(1), Loader=_FrontmatterLoader)
            if isinstance(loaded, dict):
                front = loaded
                errors.extend(_frontmatter_errors(front))
            elif loaded is not None:
                errors.append("frontmatter must be a YAML mapping")
        except (yaml.YAMLError, ValueError, TypeError) as error:
            # A broken block is left empty rather than raising: the file still
            # holds the user's notes, and losing those to a stray colon would
            # be the worse outcome.
            front = {}
            errors.append(f"invalid YAML: {_yaml_error(error)}")
    elif text.startswith("---"):
        errors.append("unterminated YAML frontmatter")

    source, notes, comments, drafts, has_comment_region, agent_block, comment_errors = _split_bodies(body)
    errors.extend(comment_errors)
    return IssueFile(
        front,
        source,
        notes,
        comments=comments,
        comment_drafts=drafts,
        has_comment_region=has_comment_region,
        agent_block=agent_block,
        errors=tuple(errors),
    )


class _FrontmatterLoader(yaml.SafeLoader):
    """Safe YAML loader that keeps dates textual and refuses duplicate keys."""

    yaml_implicit_resolvers = {
        key: [resolver for resolver in values if resolver[0] != "tag:yaml.org,2002:timestamp"]
        for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def _construct_unique_mapping(
    loader: _FrontmatterLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing frontmatter",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_FrontmatterLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _frontmatter_errors(front: dict[str, Any]) -> list[str]:
    try:
        EditableFrontmatter.model_validate(front)
    except ValidationError as error:
        messages: list[str] = []
        for detail in error.errors(include_url=False):
            field = ".".join(str(part) for part in detail["loc"]) or "frontmatter"
            messages.append(f"{field}: {detail['msg']}")
        return messages
    return []


def _yaml_error(error: Exception) -> str:
    problem = getattr(error, "problem", "")
    return str(problem or error).splitlines()[0][:160]


def _render_remote_comment(comment: RemoteComment) -> list[str]:
    attributes = _attributes(
        id=comment.comment_id,
        issue=comment.issue_id,
        author=comment.author,
        author_id=comment.author_id,
        created=_iso(comment.created_at),
        updated=_iso(comment.updated_at),
        url=comment.url,
        parent_id=comment.parent_id,
        deleted="true" if comment.deleted else "",
    )
    return [
        f"<!-- pykantui:comment {attributes} -->",
        _escape_comment_body(comment.body),
        f'<!-- pykantui:comment-end id="{escape(_marker_value(comment.comment_id), quote=True)}" -->',
    ]


def _render_comment_draft(draft: CommentDraft) -> list[str]:
    draft_id = escape(_marker_value(draft.local_id), quote=True)
    attributes = _attributes(
        id=draft.local_id,
        issue=draft.issue_id,
        created=_iso(draft.created_at),
    )
    return [
        f"<!-- pykantui:comment-draft {attributes} -->",
        _escape_comment_body(draft.body),
        f'<!-- pykantui:comment-draft-end id="{draft_id}" -->',
    ]


def _attributes(**values: str) -> str:
    return " ".join(
        f'{name.replace("_", "-")}="{escape(_marker_value(value), quote=True)}"'
        for name, value in values.items()
        if value
    )


def _marker_value(value: str) -> str:
    """Collapse untrusted metadata to one terminal-safe marker attribute."""
    return " ".join(_CONTROL_RE.sub("", _ANSI_RE.sub("", value)).split())


def format_agent_block(blocked_by: Sequence[str] = (), assigned_agent: str = "") -> str:
    """Build the ``pykantui:agent`` marker's attribute text.

    Local-only metadata for MCP-driven workflows -- never read from or
    written to a provider, exactly like ``pykantui:notes``. Empty when
    neither field has a value, so a card that never uses this stays
    byte-identical to one written before the feature existed.
    """
    return _attributes(
        blocked_by=", ".join(item.strip() for item in blocked_by if item.strip()),
        assigned_agent=assigned_agent.strip(),
    )


def parse_agent_block(raw: str) -> dict[str, str]:
    """Recover the attributes from a ``pykantui:agent`` marker's raw text.

    Callers should treat the result as read-only metadata: this is the file
    format's own parser, so ``pykantui.mcp`` reads it through here rather
    than hand-rolling a second one.
    """
    return {name: unescape(value) for name, value in _ATTRIBUTE_RE.findall(raw)}


def _escape_comment_body(body: str) -> str:
    """Make untrusted provider text marker-safe and terminal-safe.

    A leading slash is escaped as well, making the line transformation
    reversible even when a real comment starts with the escape prefix.
    """
    safe = _CONTROL_RE.sub("", _ANSI_RE.sub("", body.replace("\r\n", "\n").replace("\r", "\n")))
    lines: list[str] = []
    for line in safe.split("\n"):
        if line.startswith("\\") or re.match(r"\s*<!--\s*pykantui:", line):
            line = "\\" + line
        lines.append(line)
    return "\n".join(lines).strip()


def _unescape_comment_body(body: str) -> str:
    lines: list[str] = []
    for line in body.strip().split("\n"):
        if line.startswith("\\\\") or re.match(r"\\\s*<!--\s*pykantui:", line):
            line = line[1:]
        lines.append(line)
    return _CONTROL_RE.sub("", _ANSI_RE.sub("", "\n".join(lines))).strip()


def _parse_remote_comments(text: str) -> tuple[tuple[RemoteComment, ...], tuple[str, ...]]:
    records, errors = _record_blocks(text, _COMMENT_START_RE, "comment")
    comments: list[RemoteComment] = []
    seen: set[str] = set()
    for attributes, body in records:
        comment_id = attributes.get("id", "")
        if not comment_id:
            errors.append("provider comment is missing id")
            continue
        if comment_id in seen:
            errors.append(f"duplicate provider comment id {comment_id!r}")
            continue
        seen.add(comment_id)
        try:
            comments.append(
                RemoteComment(
                    comment_id=comment_id,
                    issue_id=attributes.get("issue", ""),
                    body=_unescape_comment_body(body),
                    author=attributes.get("author", ""),
                    author_id=attributes.get("author-id", ""),
                    created_at=_datetime_attribute(attributes.get("created", "")),
                    updated_at=_datetime_attribute(attributes.get("updated", "")),
                    url=attributes.get("url", ""),
                    parent_id=attributes.get("parent-id", ""),
                    deleted=attributes.get("deleted", "").lower() == "true",
                )
            )
        except (ValidationError, ValueError) as error:
            errors.append(f"invalid provider comment {comment_id!r}: {_yaml_error(error)}")
    return tuple(comments), tuple(errors)


def _parse_comment_drafts(text: str) -> tuple[tuple[CommentDraft, ...], tuple[str, ...]]:
    records, errors = _record_blocks(text, _COMMENT_DRAFT_START_RE, "comment-draft")
    drafts: list[CommentDraft] = []
    seen: set[str] = set()
    for attributes, body in records:
        draft_id = attributes.get("id", "")
        if not draft_id:
            errors.append("comment draft is missing id")
            continue
        if draft_id in seen:
            errors.append(f"duplicate comment draft id {draft_id!r}")
            continue
        seen.add(draft_id)
        try:
            drafts.append(
                CommentDraft(
                    local_id=draft_id,
                    issue_id=attributes.get("issue", ""),
                    body=_unescape_comment_body(body),
                    created_at=(
                        _datetime_attribute(attributes.get("created", ""))
                        or datetime.now(UTC)
                    ),
                )
            )
        except (ValidationError, ValueError) as error:
            errors.append(f"invalid comment draft {draft_id!r}: {_yaml_error(error)}")
    return tuple(drafts), tuple(errors)


def _datetime_attribute(value: str) -> datetime | None:
    """Parse one ISO marker timestamp without relying on Pydantic coercion."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _record_blocks(
    text: str,
    start_pattern: re.Pattern[str],
    kind: str,
) -> tuple[list[tuple[dict[str, str], str]], list[str]]:
    records: list[tuple[dict[str, str], str]] = []
    errors: list[str] = []
    matches = list(start_pattern.finditer(text))
    for match in matches:
        attributes = {
            name: unescape(value) for name, value in _ATTRIBUTE_RE.findall(match.group(1))
        }
        record_id = attributes.get("id", "")
        end = re.compile(
            rf'(?m)^\s*<!--\s*pykantui:{re.escape(kind)}-end\s+'
            rf'id="{re.escape(escape(record_id, quote=True))}"\s*-->\s*$'
        ).search(text, match.end())
        if end is None:
            errors.append(f"{kind.replace('-', ' ')} {record_id!r} is missing its end marker")
            continue
        body = text[match.end() : end.start()].strip()
        records.append((attributes, body))
    return records, errors


def _split_bodies(
    body: str,
) -> tuple[
    str,
    str,
    tuple[RemoteComment, ...],
    tuple[CommentDraft, ...],
    bool,
    str,
    tuple[str, ...],
]:
    match = _NOTES_RE.search(body)
    if match:
        owned, notes = body[: match.start()], body[match.end() :]
    else:
        owned, notes = body, ""

    # Extracted up front, independent of where it actually sits relative to
    # the comment region, so a hand-reordered file cannot corrupt comment or
    # draft parsing by leaving this line as unrecognised trailing text.
    agent_match = _AGENT_RE.search(owned)
    if agent_match:
        agent_block = agent_match.group(1).strip()
        owned = owned[: agent_match.start()] + owned[agent_match.end() :]
    else:
        agent_block = ""

    comments_match = _COMMENTS_RE.search(owned)
    drafts_match = _COMMENT_DRAFTS_RE.search(owned)
    comments: tuple[RemoteComment, ...] = ()
    drafts: tuple[CommentDraft, ...] = ()
    errors: list[str] = []
    has_comment_region = comments_match is not None

    if comments_match is not None:
        if drafts_match is None or drafts_match.start() < comments_match.end():
            errors.append("comment region is missing its comment-drafts marker")
            source = owned[: comments_match.start()]
        else:
            source = owned[: comments_match.start()]
            comments, comment_errors = _parse_remote_comments(
                owned[comments_match.end() : drafts_match.start()]
            )
            drafts, draft_errors = _parse_comment_drafts(owned[drafts_match.end() :])
            errors.extend(comment_errors)
            errors.extend(draft_errors)
    else:
        source = owned
        if drafts_match is not None:
            errors.append("comment-drafts marker has no comments marker")

    source = _SOURCE_RE.sub("", source, count=1)
    source = _without_heading(source)
    return (
        source.strip(),
        notes.strip(),
        comments,
        drafts,
        has_comment_region,
        agent_block,
        tuple(errors),
    )


def read(path: Path) -> IssueFile:
    return parse(path.read_text(encoding="utf-8"))


def edit_from(file: IssueFile, *, column_id: str, previous: RemoteIssue) -> IssueEdit:
    """Work out what a person changed in this file.

    Diffed against ``previous`` -- the snapshot from the last sync -- rather
    than against whatever the tracker holds now. That is the distinction that
    matters: it separates "you edited this" from "the board moved on", so a
    sync pushes only your changes and leaves theirs alone.

    ``column_id`` comes from the file's directory, not from its frontmatter.
    Moving the file *is* moving the card.
    """
    values: dict[str, Any] = {}

    # The title is never cleared by deleting its line. A card with no title is
    # not something anyone means to ask for, and the line is easy to lose to a
    # careless edit -- so a missing title reads as "leave it", not "erase it".
    title = _text(file.front.get("title"))
    if title:
        values["title"] = title

    # Everything else: **a missing key means cleared.** The writer emits these
    # whenever the tracker has a value, so a key that is gone was deleted by
    # hand. Reading absence as "leave alone" instead would make a field
    # impossible to clear from the markdown at all.
    #
    # Safe for a tracker that never sets the field: the snapshot is empty too,
    # so the diff finds no change and nothing is sent.
    for name in ("assignee", "priority"):
        values[name] = _text(file.front.get(name))

    values["issue_type"] = _text(file.front.get("type"))

    labels = file.front.get("labels")
    values["labels"] = (
        tuple(str(item) for item in labels if str(item).strip()) if isinstance(labels, (list, tuple)) else ()
    )

    components = file.front.get("components")
    values["components"] = (
        tuple(str(item) for item in components if str(item).strip())
        if isinstance(components, (list, tuple))
        else ()
    )

    values["due_date"] = _as_date(file.front.get("due") or file.front.get("due_date"))

    values["body"] = file.source
    values["column_id"] = column_id

    current = previous.model_copy(update=values)
    return IssueEdit.changed(previous, current)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None
