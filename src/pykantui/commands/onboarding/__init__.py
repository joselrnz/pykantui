"""Provider-neutral building blocks for the interactive setup journey."""

from pykantui.commands.onboarding.connection import connect_and_discover
from pykantui.commands.onboarding.credentials import (
    choose_persistence,
    collect_credentials,
)
from pykantui.commands.onboarding.models import CredentialPersistence, CredentialSetup, CredentialSource
from pykantui.commands.onboarding.projects import (
    ProjectMatch,
    choice_field,
    match_configured_project,
    normalize_projects,
    project_blurb,
    project_config,
    project_noun,
    project_scope_label,
)

__all__ = [
    "CredentialPersistence",
    "CredentialSetup",
    "CredentialSource",
    "ProjectMatch",
    "choose_persistence",
    "choice_field",
    "collect_credentials",
    "connect_and_discover",
    "match_configured_project",
    "normalize_projects",
    "project_blurb",
    "project_config",
    "project_noun",
    "project_scope_label",
]
