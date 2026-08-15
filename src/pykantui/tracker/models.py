"""Provider-neutral objects.

Every provider maps its own wire format onto these, and nothing above this
layer ever sees a Jira issue dict or a Trello card dict. That is what lets one
exporter, one backend and one wizard serve all of them.

These are deliberately *not* :class:`pykantui.models.Task`. A ``Task`` is what
the board manipulates and what the JSON store round-trips; a ``RemoteIssue`` is
what a provider reports. Keeping them apart means a provider can carry fields
the board has no opinion about — a Trello card's position, a Jira epic link —
without either growing a field for the other's benefit.

Pydantic models rather than dataclasses, matching :mod:`pykantui.models`. The
validation is the point, not the ceremony: these objects are built straight out
of JSON that a remote service just handed us, so this is exactly the boundary
where a missing key or a surprise ``None`` should be caught and named.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pykantui.core.naming import safe_name as safe_name


#: Normalised column meanings. Providers each have their own vocabulary --
#: Jira has status categories, Plane has ``state_group``, Trello has nothing at
#: all -- so the mapping onto pykantui's start/finish columns needs one shared
#: set of names to go through.
class ColumnGroup(StrEnum):
    """Provider-neutral workflow groups used by every board."""

    BACKLOG = "backlog"
    TODO = "todo"
    STARTED = "started"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


COLUMN_BACKLOG = ColumnGroup.BACKLOG
COLUMN_TODO = ColumnGroup.TODO
COLUMN_STARTED = ColumnGroup.STARTED
COLUMN_REVIEW = ColumnGroup.REVIEW
COLUMN_DONE = ColumnGroup.DONE
COLUMN_CANCELLED = ColumnGroup.CANCELLED
COLUMN_UNKNOWN = ColumnGroup.UNKNOWN

COLUMN_GROUPS = tuple(ColumnGroup)


class RemoteModel(BaseModel):
    """Shared configuration for everything a provider returns.

    Frozen, because these describe what the remote said at one moment; a caller
    that wants a changed copy should say so with ``model_copy(update=...)``
    rather than mutating something another part of the sync is still reading.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", str_strip_whitespace=True)


class RemoteUser(RemoteModel):
    """Whoever the credentials belong to.

    Returned by :meth:`Provider.verify` so the wizard can say "connected as
    alex" rather than "connected", which is the difference between catching a
    wrong-account mistake now and catching it after the first sync.
    """

    account_id: str = ""
    display_name: str = ""
    email: str = ""

    #: The handle a tracker knows you by where that is not the display name --
    #: a GitHub login, a ClickUp username. Kept separate because it is unique
    #: and stable where a display name is neither.
    username: str = ""

    def label(self) -> str:
        return self.display_name or self.email or self.account_id or "unknown account"


class RemoteComment(RemoteModel):
    """One immutable provider comment, normalised for Markdown and the TUI.

    Comments deliberately live outside :class:`RemoteIssue`.  A newly posted
    comment must not make every card look like a provider-side field conflict,
    and storing an entire discussion thread in the issue snapshot would make
    ``state.json`` grow without bound.
    """

    comment_id: str = Field(min_length=1)
    issue_id: str
    body: str = Field(default="", max_length=1_000_000)
    author: str = ""
    author_id: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    url: str = ""
    parent_id: str = ""
    deleted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def pending(self) -> bool:
        """Provider comments are already remote and never pending."""

        return False


class CommentDraft(RemoteModel):
    """One local append-only comment waiting for a confirmed Sync."""

    local_id: str
    issue_id: str
    body: str = Field(min_length=1, max_length=100_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("body")
    @classmethod
    def _body_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("comment body cannot be blank")
        return value

    @property
    def comment_id(self) -> str:
        """Expose the same stable identity used by rendered remote comments."""

        return self.local_id

    @property
    def author(self) -> str:
        """Local drafts are visibly attributed without impersonating a user."""

        return "You"

    @property
    def author_id(self) -> str:
        return ""

    @property
    def updated_at(self) -> datetime:
        return self.created_at

    @property
    def url(self) -> str:
        return ""

    @property
    def parent_id(self) -> str:
        return ""

    @property
    def pending(self) -> bool:
        """A draft remains local until its confirmed provider write succeeds."""

        return True


class RemoteProject(RemoteModel):
    """A project, board or space -- whatever the provider calls its container."""

    project_id: str
    key: str = ""
    name: str = ""
    description: str = ""

    #: Where a human would go to look at this in a browser.
    url: str = ""

    #: The account or group this project belongs to, where the tracker has one.
    #: GitHub does -- its project names are ``owner/repo``, which
    #: cannot be a single directory -- and nobody else does.
    owner: str = ""

    #: Provider-specific extras the wizard may want to show but nothing else
    #: interprets: Jira's board type, Trello's organisation, and so on.
    extra: dict[str, Any] = Field(default_factory=dict)

    def slug(self) -> str:
        """A filesystem-safe directory name for this project.

        Prefers the key, because ``projects/JPT/`` is worth more to a human
        reading a file tree than ``projects/11111111-2222-3333-.../``.
        """
        return safe_name(self.key or self.name or self.project_id)

    def path_parts(self) -> tuple[str, ...]:
        """The directory segments this project lives under.

        One segment for most trackers. Two for GitHub, whose projects are
        ``owner/repo``: flattening that to ``acme-widgets``
        would lose a real distinction and read badly, and grouping repos by
        owner is how people actually think about them.
        """
        if self.owner:
            return (safe_name(self.owner), self.slug())
        return (self.slug(),)

    def label(self) -> str:
        if self.key and self.name and self.key != self.name:
            return f"{self.key} — {self.name}"
        return self.name or self.key or self.project_id


class IssueType(RemoteModel):
    """One kind of issue a project accepts -- Story, Bug, Epic, whatever it calls them.

    Discovered per project rather than assumed. Two Jira projects in the same
    site routinely offer different sets, so a list hardcoded anywhere would be
    wrong for somebody the first time it was used.

    An empty list from :meth:`~pykantui.tracker.base.Provider.list_issue_types`
    means "this tracker has no such concept, or will not tell us" -- which is
    different from "there are none", and is why callers must treat it as
    "do not send a type" rather than as an error.
    """

    type_id: str
    name: str = ""

    #: Needs a parent, so it cannot be created on its own. Excluded from the
    #: choices offered for a new story.
    subtask: bool = False

    #: Where this sits in the hierarchy: ``0`` is an ordinary issue, ``-1`` and
    #: below need a parent, ``1`` and above are containers like an epic.
    #:
    #: Normalised from whatever the tracker calls it, and ``0`` where it says
    #: nothing -- so a tracker with no hierarchy has every type at the level
    #: that "an ordinary issue" means, and the same default logic still works.
    level: int = 0

    #: What the project uses when none is given.
    default: bool = False


class IssueComponent(RemoteModel):
    """One project-scoped component accepted by providers that expose them."""

    component_id: str
    name: str = ""
    description: str = ""


class RemoteColumn(RemoteModel):
    """One column, list or status the provider groups issues by."""

    column_id: str
    name: str = ""
    position: int = 0

    #: One of :data:`COLUMN_GROUPS`. The provider decides; where it genuinely
    #: cannot tell, :data:`COLUMN_UNKNOWN` is honest and the caller falls back
    #: to matching on the name.
    group: str = COLUMN_UNKNOWN

    #: Provider status ids that land in this column. Jira boards map several
    #: statuses onto one column, so this is a list rather than an id.
    status_ids: tuple[str, ...] = ()

    @field_validator("group")
    @classmethod
    def _known_group(cls, value: str) -> str:
        """Reject a group nobody downstream knows how to interpret.

        A typo'd group would otherwise fail silently and much later, as a
        column that never matches the start or finish rule.
        """
        if value not in COLUMN_GROUPS:
            raise ValueError(f"group must be one of {', '.join(COLUMN_GROUPS)}, not {value!r}")
        return value

    def holds(self, status_id: str) -> bool:
        return status_id == self.column_id or status_id in self.status_ids


class RemoteIssue(RemoteModel):
    """One issue, card or work item.

    ``body`` is already markdown. Converting out of the provider's own format --
    ADF, wiki markup, HTML -- happens in the provider, using
    :mod:`pykantui.tracker.markup`, so that nothing downstream has to care
    which of the three it started as.
    """

    issue_id: str
    key: str = ""
    title: str = ""
    column_id: str = ""

    body: str = ""
    issue_type: str = ""
    status: str = ""
    priority: str = ""
    assignee: str = ""
    reporter: str = ""

    #: The provider's own identifiers for those people -- a Jira ``accountId``,
    #: a Plane member UUID -- kept beside the display names rather than instead
    #: of them.
    #:
    #: Both are needed and they answer different questions. The display name is
    #: what a markdown file should show a human; the id is the only safe way to
    #: ask "is this mine". Two colleagues called "Alex" is not hypothetical, and
    #: matching on a name means a rename silently empties your board.
    #:
    #: A tuple for assignees because several trackers allow more than one --
    #: GitHub, ClickUp, Plane and Shortcut all do -- and collapsing that
    #: to a single id would drop you from a card you share.
    assignee_ids: tuple[str, ...] = ()
    reporter_id: str = ""

    labels: tuple[str, ...] = ()
    components: tuple[str, ...] = ()

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    due_date: date | None = None

    #: Key of the epic/parent card, where the provider has a hierarchy.
    parent_key: str = ""

    #: Ordering within the column, where the provider keeps one. Jira does not.
    position: float | None = None

    url: str = ""

    #: Anything provider-specific worth writing into the markdown frontmatter
    #: but not worth a field here. Ends up under an ``extra:`` key, so it
    #: round-trips without colliding with the names above.
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", mode="before")
    @classmethod
    def _title_is_one_line(cls, value: Any) -> Any:
        """Collapse a multi-line summary.

        The title becomes a YAML frontmatter value and a card's first line; an
        embedded newline breaks the first and truncates the second.
        """
        if isinstance(value, str) and "\n" in value:
            return " ".join(value.split())
        return value

    @field_validator("labels", "components", mode="before")
    @classmethod
    def _drop_empty_names(cls, value: Any) -> Any:
        """Drop blank collection entries before writing them to frontmatter."""
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value if str(item).strip())
        return value

    def filename(self) -> str:
        """The markdown file this issue is written to.

        A tracker with human keys gets ``JPT-4.md`` and nothing more: the key
        already says what the card is, and appending the title would churn the
        filename every time someone renames a card.

        Asana, Monday and Trello have no such key -- their ids are bare digits
        -- so a title slug is appended to make the tree browsable. The id stays
        in front, so files still sort by age and stay unique when two cards
        share a title.
        """
        stem = self.key or self.issue_id
        if not _has_letter(stem):
            slug = slugify(self.title)
            if slug:
                stem = f"{stem}-{slug}"
        return f"{safe_name(stem)}.md"

    def display_key(self) -> str:
        return self.key or self.issue_id


class IssueDraft(RemoteModel):
    """An issue that does not exist yet.

    Deliberately not a :class:`RemoteIssue` with blank ids. A draft has no key,
    no id and no url because the tracker assigns those, and a model that
    pretends otherwise invites code to read an id that is not there yet.
    """

    title: str
    body: str = ""
    issue_type: str = ""
    column_id: str = ""
    priority: str = ""
    labels: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    due_date: date | None = None
    parent_key: str = ""

    #: Who the work is for, as a display name -- what the markdown shows.
    assignee: str = ""

    #: Who the work is for, as provider ids. Defaults to you at the command
    #: line, because a story you sat down and wrote is your work -- and because
    #: a tracker sets only the *reporter* on create, so an unassigned new issue
    #: falls straight out of an assigned-only board on the next sync.
    assignee_ids: tuple[str, ...] = ()

    #: Where the draft is meant to land, as a folder name. Kept because the
    #: file is written before any tracker call, and the column it was drafted
    #: into is the user's intent even if the create later fails.
    column_name: str = ""

    def slug(self) -> str:
        """A filename for a draft, which has no key to be named after."""
        return slugify(self.title) or "untitled"


class IssueEdit(RemoteModel):
    """What changed in a markdown file, ready to send back to the tracker.

    Every field is optional and ``None`` means "not touched" -- which is why
    these cannot be plain strings with ``""`` defaults. Clearing a due date and
    leaving a due date alone are different requests, and a model that cannot
    tell them apart will silently wipe fields the user never opened.

    Use :meth:`changed` to build one from a before/after pair rather than
    constructing it by hand; that keeps "unchanged" and "cleared" straight.
    """

    title: str | None = None
    body: str | None = None
    column_id: str | None = None
    assignee: str | None = None
    labels: tuple[str, ...] | None = None
    due_date: date | None = None
    priority: str | None = None
    issue_type: str | None = None
    components: tuple[str, ...] | None = None

    #: Set when the user cleared a field rather than leaving it alone. Named
    #: fields here are sent as an explicit null, not skipped.
    cleared: tuple[str, ...] = ()

    def touched(self) -> tuple[str, ...]:
        """Which fields this edit actually carries."""
        named = tuple(name for name in EDITABLE_FIELDS if getattr(self, name, None) is not None)
        return tuple(dict.fromkeys(named + self.cleared))

    def is_empty(self) -> bool:
        return not self.touched()

    def unsupported(self, allowed: tuple[str, ...]) -> tuple[str, ...]:
        """Fields in this edit the provider cannot accept.

        Checked before anything is sent, so a partial write never happens: the
        caller is told up front rather than discovering it after two of four
        fields already landed.
        """
        return tuple(name for name in self.touched() if name not in allowed)

    @classmethod
    def changed(cls, before: RemoteIssue, after: RemoteIssue) -> IssueEdit:
        """Diff two versions of an issue into an edit.

        The whole point: a sync writes the file, the user edits it, and this
        works out what they actually changed. Fields that match are left as
        ``None`` so they are never sent, which is what stops a round-trip from
        rewriting every field of every issue on every save.
        """
        values: dict[str, Any] = {}
        cleared: list[str] = []
        for name in EDITABLE_FIELDS:
            old, new = getattr(before, name), getattr(after, name)
            if old == new:
                continue
            if new in (None, "", ()):
                cleared.append(name)
            else:
                values[name] = new
        return cls(**values, cleared=tuple(cleared))


#: The fields a local markdown edit can push back. Neutral names: each provider
#: translates them into its own PUT/PATCH body, so nothing above this layer
#: writes ``customfield_10007`` or ``description_html``.
#:
#: Derived from :class:`IssueEdit` rather than restated next to it. The two
#: would otherwise drift, and the drift is silent: a field added to the model
#: but missing here is one that no provider is ever allowed to accept.
EDITABLE_FIELDS: tuple[str, ...] = tuple(name for name in IssueEdit.model_fields if name != "cleared")


def slugify(text: str, limit: int = 48) -> str:
    """A short, lowercase, hyphenated fragment of ``text`` for a filename.

    Truncated on a word boundary rather than mid-word, because a file called
    ``1201234567-customize-your-set.md`` reads worse than one word shorter.
    Non-ASCII is dropped rather than transliterated -- these end up in git
    paths and shell commands, and an emoji in a filename helps nobody.
    """
    kept = [character.lower() if character.isalnum() and character.isascii() else "-" for character in text]
    words = [word for word in "".join(kept).split("-") if word]
    if not words:
        return ""

    slug = words[0][:limit]
    for word in words[1:]:
        if len(slug) + 1 + len(word) > limit:
            break
        slug = f"{slug}-{word}"
    return slug


def _has_letter(value: str) -> bool:
    """Whether a key carries any letter, and so reads as a human identifier.

    ``JPT-4`` and ``sc-77`` do; Asana's ``1201234567`` does not.
    """
    return any(character.isalpha() for character in value)
