"""What a workspace is pointed at, and where its credentials live.

Two files, deliberately apart:

``<workspace>/.pykantui/project.json``
    Which tracker, which project, how the columns are named. Safe to commit --
    it is the thing that lets someone else clone the repo and sync it with
    their own credentials.

``<data_dir>/auth.json``
    Tokens. Outside the workspace, owner-only, and never in git. It lives with
    the user rather than with the project because one person's token is theirs
    across every workspace they open.

The split is enforced by :class:`~pykantui.tracker.spec.ProviderSpec`, which
refuses at import to let a secret be declared as a config field. This module
just honours it.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pykantui.config.paths import write_text_atomic
from pykantui.tracker import build, get
from pykantui.tracker.base import Provider
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.mine import Identity, Scope, identify
from pykantui.tracker.models import RemoteProject
from pykantui.workspace import credentials, layout
from pykantui.workspace.credentials import (
    environment_value_trusts_scope,
    validate_provider_config,
)
from pykantui.workspace.layout import ColumnStyle

SCHEMA = 1

# Compatibility exports for callers that used the original project module.
load_secrets = credentials.load_secrets
resolve_fields = credentials.resolve_fields
save_secrets = credentials.save_secrets


class Project(BaseModel):
    """One tracker's project, as a workspace remembers it."""

    provider: str
    project_id: str
    key: str = ""
    name: str = ""

    #: Non-secret provider settings -- base URL, board id, workspace slug.
    #: Never a token; see the module docstring.
    config: dict[str, Any] = Field(default_factory=dict)

    #: How column directories are named. Stored, not assumed, so changing the
    #: default later cannot silently orphan an existing tree.
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE

    #: Who "mine" is, where the credential cannot say. Plane needs this; the
    #: other providers fill it in themselves. See docs/provider-identity.md.
    me: str = ""

    #: Which issues reach the markdown tree. ``None`` mirrors the whole
    #: project, which is what an existing workspace created before this
    #: existed must keep doing -- turning a shared mirror into a personal one
    #: on upgrade would silently archive most of somebody's board.
    scope: Scope | None = None

    created_at: datetime = Field(default_factory=datetime.now)

    def identity(self, provider: Provider) -> Identity | None:
        """Who "mine" is here, or None when the whole project is mirrored.

        Asks the tracker only when a filter is actually wanted, so an
        unfiltered workspace never pays for a ``/myself`` call.
        """
        if self.scope is None or self.scope.describes_all():
            return None
        who = None
        if not self.me:
            with contextlib.suppress(ProviderError):
                who = provider.verify()
        return identify(who, self.me)

    # ---- disk ------------------------------------------------------------

    @classmethod
    def load(cls, workspace: Path) -> Project:
        path = layout.project_file(workspace)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ProviderError(
                f"{workspace} is not a pykantui workspace",
                hint="Create one with: kbn init",
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderError(f"could not read {path}: {error}") from error

        if document.get("schema") != SCHEMA:
            raise ProviderError(
                f"{path} was written by a different version of pykantui",
                hint=f"Expected schema {SCHEMA}, found {document.get('schema')!r}.",
            )
        document.pop("schema", None)
        return cls.model_validate(document)

    def save(self, workspace: Path) -> None:
        document = {"schema": SCHEMA, **self.model_dump(mode="json")}
        write_text_atomic(layout.project_file(workspace), json.dumps(document, indent=2, ensure_ascii=False))

    # ---- using it --------------------------------------------------------

    def remote(self) -> RemoteProject:
        return RemoteProject(project_id=self.project_id, key=self.key, name=self.name)

    def open(self) -> Provider:
        """Build the provider this project points at, with its credentials.

        Credentials come from the saved auth file, falling back to the
        environment for anything it does not hold. The fallback is what makes
        this usable anywhere the auth file cannot be: a container, a CI job, a
        fresh clone driven by secrets in the environment.

        Found by running the app in Docker -- ``kbn init`` worked, because the
        wizard already consulted the environment, and ``kbn sync`` then failed
        with "Jira needs API token" in the same shell with the same variables
        set. The two commands disagreed about where a credential lives.
        """
        config = validate_provider_config(self.provider, self.config)
        return build(self.provider, config, self.secrets())

    def secrets(self) -> dict[str, str]:
        """This project's credentials: the auth file first, then the environment."""
        config = validate_provider_config(self.provider, self.config)
        found = dict(load_secrets(self.provider, config))
        for field in get(self.provider).spec.all_fields():
            if not field.secret or found.get(field.name):
                continue
            if not environment_value_trusts_scope(self.provider, config, field):
                continue
            from_env = field.from_env()
            if from_env:
                found[field.name] = from_env
        return found

    def label(self) -> str:
        return f"{self.provider}/{self.key or self.project_id}"


def missing_required(provider: str, config: dict[str, Any], secrets: dict[str, str]) -> list[str]:
    """Required fields with no value from any source.

    A ``CHOICE`` field is never reported here. Its values come from the tracker
    once connected, and this check runs *before* connecting -- so counting it
    as missing would refuse ``kbn init --type jira --token x`` even for an
    account that can see exactly one project.
    """
    from pykantui.tracker.spec import FieldKind  # noqa: PLC0415 - avoids an import cycle

    spec = get(provider).spec
    return [
        field.name
        for field in spec.required_fields()
        if field.kind is not FieldKind.CHOICE and not (config.get(field.name) or secrets.get(field.name))
    ]
