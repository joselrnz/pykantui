"""Apply a bounded AI-authored proposal to a local batch manifest."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from yaml.tokens import AliasToken, AnchorToken

from pykantui.batch.models import (
    MAX_MANIFEST_BYTES,
    BatchIssue,
    BatchManifest,
    BatchState,
    FieldSource,
    _ManifestLoader,
)
from pykantui.tracker.errors import ProviderError

REFINEMENT_KIND = "IssueBatchRefinement"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RefinementIssue(_StrictModel):
    ref: str
    title: str | None = None
    body: str | None = None
    issue_type: str | None = Field(default=None, alias="type")
    state: BatchState | None = None
    parent_ref: str | None = Field(default=None, alias="parent")
    priority: str | None = None
    labels: tuple[str, ...] | None = None
    components: tuple[str, ...] | None = None
    due_date: date | None = Field(default=None, alias="due")
    assignee: str | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _short_state(cls, value: object) -> object:
        return {"name": value} if isinstance(value, str) else value

    @model_validator(mode="after")
    def _has_change(self) -> RefinementIssue:
        if self.model_fields_set == {"ref"}:
            raise ValueError("refinement item must propose at least one field")
        missing = sorted(
            _ALIASES.get(name, name)
            for name in self.model_fields_set - {"ref"}
            if getattr(self, name) is None
        )
        if missing:
            raise ValueError(f"refinement fields must have values: {', '.join(missing)}")
        return self


class BatchRefinement(_StrictModel):
    api_version: Literal["pykantui.dev/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["IssueBatchRefinement"]
    batch: str
    issues: tuple[RefinementIssue, ...]

    @model_validator(mode="after")
    def _unique_refs(self) -> BatchRefinement:
        refs = [issue.ref for issue in self.issues]
        if len(refs) != len(set(refs)):
            raise ValueError("refinement contains duplicate refs")
        return self


_ALIASES = {
    "title": "title",
    "body": "body",
    "issue_type": "type",
    "state": "state",
    "parent_ref": "parent",
    "priority": "priority",
    "labels": "labels",
    "components": "components",
    "due_date": "due",
    "assignee": "assignee",
}


def load_refinement(path: Path) -> BatchRefinement:
    """Read strict inert YAML; aliases, duplicate keys, and unknown fields fail."""
    target = path.expanduser().resolve()
    try:
        if target.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProviderError("batch refinement file is too large")
        text = target.read_text(encoding="utf-8")
        if any(isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(text)):
            raise ProviderError("batch refinement YAML aliases and anchors are not allowed")
        raw = yaml.load(text, Loader=_ManifestLoader)
        if not isinstance(raw, dict):
            raise ProviderError("batch refinement must be a YAML mapping")
        return BatchRefinement.model_validate(raw)
    except ProviderError:
        raise
    except FileNotFoundError as error:
        raise ProviderError(f"batch refinement does not exist: {target}") from error
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError, TypeError, RecursionError) as error:
        message = str(getattr(error, "problem", "") or error).splitlines()[0]
        raise ProviderError(f"invalid batch refinement: {message}") from error


def apply_refinement(
    manifest: BatchManifest,
    proposal: BatchRefinement,
    *,
    redo_ai: bool = False,
) -> BatchManifest:
    """Fill missing fields; only prior AI fields may be replaced with ``redo_ai``."""
    if proposal.batch != manifest.metadata.name:
        raise ProviderError(
            f"refinement targets batch {proposal.batch!r}, not {manifest.metadata.name!r}"
        )
    proposed = {item.ref: item for item in proposal.issues}
    known = {item.ref for item in manifest.issues}
    unknown = sorted(set(proposed) - known)
    if unknown:
        raise ProviderError(f"refinement references unknown issues: {', '.join(unknown)}")

    updated: list[BatchIssue] = []
    changed = 0
    for issue in manifest.issues:
        suggestion = proposed.get(issue.ref)
        if suggestion is None:
            updated.append(issue)
            continue
        values: dict[str, object] = {}
        sources = dict(issue.sources)
        for field_name in suggestion.model_fields_set - {"ref"}:
            alias = _ALIASES[field_name]
            current = getattr(issue, field_name)
            if not _may_replace(manifest, issue, field_name, current, redo_ai=redo_ai):
                raise ProviderError(
                    f"{issue.ref}: AI refinement cannot replace {alias!r}",
                    hint="Remove that field from the proposal, or use --redo-ai only for prior AI fields.",
                )
            values[field_name] = getattr(suggestion, field_name)
            sources[alias] = FieldSource.AI
            changed += 1
        values["sources"] = sources
        updated.append(issue.model_copy(update=values))

    if not changed:
        raise ProviderError("refinement did not change any fields")
    return BatchManifest.model_validate(
        manifest.model_dump(mode="python", by_alias=True) | {"issues": updated}
    )


def _may_replace(
    manifest: BatchManifest,
    issue: BatchIssue,
    field_name: str,
    current: object,
    *,
    redo_ai: bool,
) -> bool:
    alias = _ALIASES[field_name]
    if redo_ai and issue.sources.get(alias) is FieldSource.AI:
        return True
    if current not in (None, "", (), []):
        return False
    defaults = manifest.defaults
    effective_default = {
        "issue_type": defaults.issue_type,
        "state": defaults.state,
        "priority": defaults.priority,
        "labels": defaults.labels,
        "components": defaults.components,
    }.get(field_name)
    return effective_default in (None, "", (), [])


__all__ = ["BatchRefinement", "RefinementIssue", "apply_refinement", "load_refinement"]
