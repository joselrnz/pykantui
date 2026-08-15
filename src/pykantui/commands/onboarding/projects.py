"""Provider-neutral discovery rules for selecting one remote work container.

Providers do not agree on the name of the thing that owns a board: GitHub
uses a repository, Linear a team, ClickUp a list, and Shortcut a workflow.
The provider's dynamic ``CHOICE`` field is the source of truth, while this
module owns the safety rules shared by both onboarding paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pykantui.tracker.models import RemoteProject
from pykantui.tracker.spec import FieldKind, ProviderField, ProviderSpec

_PLURALS = {
    "repository": "repositories",
    "project": "projects",
    "list": "lists",
    "board": "boards",
    "team": "teams",
    "workflow": "workflows",
}


class ProjectMatch(StrEnum):
    """How a configured value relates to the current discovery response."""

    NONE = "none"
    EXACT_ID = "exact_id"
    EXACT_KEY = "exact_key"
    EXACT_NAME = "exact_name"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ProjectMatchResult:
    """One safe configured match, or why there is not one."""

    kind: ProjectMatch
    project: RemoteProject | None = None


def choice_field(spec: ProviderSpec) -> ProviderField | None:
    """Return the provider field whose values come from remote discovery."""

    return next((field for field in spec.config_fields if field.kind is FieldKind.CHOICE), None)


def project_scope_label(spec: ProviderSpec) -> str:
    """Return the provider-native, title-cased work-container label."""

    field = choice_field(spec)
    if field is None:
        return "Project"
    label = field.label.strip() or "Project"
    if label.casefold().endswith(" key"):
        label = label[:-4].rstrip()
    return label


def project_noun(spec: ProviderSpec, *, count: int) -> str:
    """Return a lower-case singular or plural provider-native noun."""

    singular = project_scope_label(spec).casefold()
    if count == 1:
        return singular
    return _PLURALS.get(singular, f"{singular}s")


def normalize_projects(projects: list[RemoteProject]) -> list[RemoteProject]:
    """Deduplicate remote ids and return a deterministic, searchable order."""

    unique: dict[str, RemoteProject] = {}
    for project in projects:
        unique.setdefault(project.project_id, project)
    return sorted(
        unique.values(),
        key=lambda project: (
            (project.name or project.key or project.project_id).casefold(),
            project.key.casefold(),
            project.project_id.casefold(),
        ),
    )


def match_configured_project(value: str, projects: list[RemoteProject]) -> ProjectMatchResult:
    """Resolve a configured id/key/name without guessing between duplicates."""

    wanted = value.strip()
    if not wanted:
        return ProjectMatchResult(ProjectMatch.NONE)
    tiers = (
        (ProjectMatch.EXACT_ID, [project for project in projects if project.project_id == wanted]),
        (ProjectMatch.EXACT_KEY, [project for project in projects if project.key == wanted]),
        (ProjectMatch.EXACT_NAME, [project for project in projects if project.name == wanted]),
    )
    for kind, matches in tiers:
        if len(matches) == 1:
            return ProjectMatchResult(kind, matches[0])
        if len(matches) > 1:
            return ProjectMatchResult(ProjectMatch.AMBIGUOUS)
    return ProjectMatchResult(ProjectMatch.NONE)


def project_config(
    spec: ProviderSpec,
    config: dict[str, object],
    project: RemoteProject,
) -> dict[str, object]:
    """Return config pinned to the selected remote container."""

    updated = dict(config)
    field = choice_field(spec)
    if field is None:
        return updated
    updated[field.name] = project.key if field.name.endswith("_key") and project.key else project.project_id
    return updated


def project_context(project: RemoteProject) -> tuple[str, ...]:
    """Human context that distinguishes resources with identical names."""

    context: list[str] = []
    workspace = project.extra.get("workspace_name") or project.extra.get("space_name")
    if workspace:
        context.append(str(workspace))
    if project.owner:
        context.append(project.owner)
    return tuple(dict.fromkeys(part for part in context if part))


def project_blurb(project: RemoteProject) -> str:
    """Describe one discovered resource without leaking credentials."""

    lines = [project.description.strip().splitlines()[0]] if project.description.strip() else []
    context = project_context(project)
    if context:
        lines.append(" · ".join(context))
    lines.append(f"id: {project.project_id}")
    if project.url:
        lines.append(project.url)
    return "\n".join(lines)


__all__ = [
    "ProjectMatch",
    "ProjectMatchResult",
    "choice_field",
    "match_configured_project",
    "normalize_projects",
    "project_blurb",
    "project_config",
    "project_context",
    "project_noun",
    "project_scope_label",
]
