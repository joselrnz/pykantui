"""User-level index of provider projects and their local workspace paths."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pykantui.config.paths import projects_path, write_text_atomic
from pykantui.tracker.errors import ProviderError

if TYPE_CHECKING:
    from pykantui.workspace.project import Project

REGISTRY_SCHEMA = 1
REGISTRY_LOCK_TIMEOUT_SECONDS = 10


class ProjectLink(BaseModel):
    """Safe, non-secret metadata locating one initialized workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider: str
    project_id: str
    key: str = ""
    name: str = ""
    workspace: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def workspace_path(self) -> Path:
        """Canonical local directory chosen by the user during initialization."""

        return Path(self.workspace)

    @property
    def available(self) -> bool:
        """Whether the recorded workspace currently exists on this machine."""

        return self.workspace_path.is_dir()


class ProjectRegistry(BaseModel):
    """Versioned registry document stored at ``~/.pykantui/projects.json``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schema")
    projects: list[ProjectLink] = Field(default_factory=list)


def load_registry(path: Path | None = None) -> ProjectRegistry:
    """Load the project registry without hiding corruption or schema drift."""

    target = path or projects_path()
    if not target.exists():
        return ProjectRegistry()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return ProjectRegistry.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ProviderError(
            f"could not read project registry {target}",
            hint="Repair or move projects.json; pykantui did not overwrite it.",
        ) from error


def register_workspace(workspace: Path, project: Project) -> ProjectLink:
    """Atomically add or update one workspace while preserving other entries."""

    target = projects_path()
    canonical = workspace.expanduser().resolve(strict=False)
    link = ProjectLink(
        provider=project.provider,
        project_id=project.project_id,
        key=project.key,
        name=project.name,
        workspace=str(canonical),
    )
    lock = FileLock(str(target.with_suffix(f"{target.suffix}.lock")), timeout=REGISTRY_LOCK_TIMEOUT_SECONDS)
    try:
        with lock:
            registry = load_registry(target)
            registry.projects = [item for item in registry.projects if item.workspace_path != canonical]
            registry.projects.append(link)
            registry.projects.sort(key=lambda item: (item.provider.casefold(), item.key.casefold(), item.workspace))
            write_text_atomic(target, registry.model_dump_json(indent=2, by_alias=True))
    except Timeout as error:
        raise ProviderError(
            "project registry is busy",
            hint="Wait for the other pykantui process to finish, then retry.",
        ) from error
    return link


__all__ = ["ProjectLink", "ProjectRegistry", "load_registry", "register_workspace"]
