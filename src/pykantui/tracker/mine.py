"""Deciding which issues are yours.

You asked for a board that is only your work: the cache holds the whole
project, the markdown tree holds your cards and nothing else.

Two rules carry the whole module, and both exist because the failure mode is
silent.

**Match on the provider's own id, never on a display name.** Two colleagues
called "Alex" is not hypothetical, and a rename would quietly empty your board
overnight with no error to explain it. Email and username are accepted as
*fallbacks* for trackers that expose no usable id, and a display name is never
matched at all.

**An unresolvable identity is a refusal, not a guess.** Getting "who am I"
wrong does not produce an error, it produces the wrong board -- either empty,
or full of somebody else's work -- and you would not necessarily notice which.
So this raises rather than falls back to "everything".
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import RemoteIssue, RemoteUser


class Scope(BaseModel):
    """What "mine" includes.

    Defaults to **assigned only**, because that is the work you actually have
    to do. Reporting an issue is not doing it: on a team board most of what you
    raise is picked up by somebody else, and counting it as yours fills your
    desk with other people's work in progress.

    ``reported`` is there for the boards where it is the right answer -- one you
    run yourself, where raising a card and owning it are the same act.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    assigned: bool = True
    reported: bool = False

    #: Off by default: an empty scope would silently match nothing, and a
    #: board that is mysteriously empty is worse than one that is too full.
    everything: bool = False

    def describes_all(self) -> bool:
        return self.everything

    def is_empty(self) -> bool:
        return not (self.assigned or self.reported or self.everything)


class Identity(BaseModel):
    """Who you are on one tracker, in every form it might be recognised by.

    Several handles rather than one because trackers disagree about what a
    person *is*: Jira has an opaque ``accountId``, GitHub a numeric id and a
    login, Plane a member UUID and an email. Any of them identifying the same
    person is enough.
    """

    model_config = {"frozen": True}

    account_id: str = ""
    email: str = ""
    username: str = ""
    display_name: str = ""

    #: Extra ids for the same person -- a second account, or the id a
    #: particular project knows them by.
    also: tuple[str, ...] = Field(default=())

    @classmethod
    def of(cls, user: RemoteUser, *, also: tuple[str, ...] = ()) -> Identity:
        return cls(
            account_id=user.account_id,
            email=user.email,
            username=user.username,
            display_name=user.display_name,
            also=also,
        )

    def handles(self) -> set[str]:
        """Everything that counts as "me", lowercased for comparison.

        The display name is deliberately absent. It is the one handle that is
        neither unique nor stable, and matching on it is how a board silently
        fills with a stranger's work.
        """
        found = {self.account_id, self.email, self.username, *self.also}
        return {handle.strip().casefold() for handle in found if handle and handle.strip()}

    def is_resolved(self) -> bool:
        return bool(self.handles())


def identify(user: RemoteUser | None, configured: str = "") -> Identity:
    """Work out who you are, preferring what you configured.

    ``configured`` wins because some trackers cannot answer at all -- a Plane
    API key is workspace-scoped and identifies no person -- and because a
    machine account may act on your behalf.

    Raises when neither source produces a usable handle. That is the point:
    silently treating "unknown" as "everything" would hand you the whole team's
    board and look like it worked.
    """
    settled = configured.strip()
    identity = Identity.of(user) if user is not None else Identity()
    if settled:
        identity = identity.model_copy(update={"also": (*identity.also, settled)})

    if not identity.is_resolved():
        raise ProviderError(
            "cannot tell which issues are yours: this tracker did not say who you are",
            hint=(
                "Set 'me' in .pykantui/project.json to your email, username or "
                "account id — or pass --all to mirror the whole project."
            ),
        )
    return identity


#: Assigned-to-me or reported-by-me: the rule you asked for, and the default
#: everywhere one is needed.
DEFAULT_SCOPE = Scope()


def owns(issue: RemoteIssue, identity: Identity, scope: Scope | None = None) -> bool:
    """Whether this issue counts as yours under ``scope``."""
    scope = scope or DEFAULT_SCOPE
    if scope.everything:
        return True

    handles = identity.handles()
    if scope.assigned and _matches(issue.assignee_ids, handles):
        return True
    if scope.assigned and _matches(_split(issue.assignee), handles):
        # Only reached where the provider exposes no ids at all. Restricted to
        # email and username shapes by `handles()` never holding a display
        # name, so this cannot match one "Alex" against another.
        return True
    if scope.reported and issue.reporter_id and issue.reporter_id.casefold() in handles:
        return True
    return bool(scope.reported and _matches(_split(issue.reporter), handles))


def _matches(values: tuple[str, ...] | list[str], handles: set[str]) -> bool:
    return any(value.strip().casefold() in handles for value in values if value.strip())


def _split(value: str) -> list[str]:
    """A display field that may hold several people, comma-joined."""
    return [part.strip() for part in value.split(",") if part.strip()]
