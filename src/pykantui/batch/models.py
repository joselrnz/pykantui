"""Strict, non-executable YAML contract for declarative issue batches."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken

from pykantui.config.paths import write_text_atomic
from pykantui.tracker.errors import ProviderError

API_VERSION = "pykantui.dev/v1alpha1"
KIND = "IssueBatch"
MAX_MANIFEST_BYTES = 1_048_576
MAX_BATCH_ISSUES = 100
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FieldSource(StrEnum):
    USER = "user"
    AI = "ai"
    GENERATOR = "generator"


class BatchMetadata(StrictModel):
    name: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not _REF.fullmatch(normalized):
            raise ValueError("must be 1-64 letters, numbers, dots, dashes, or underscores")
        return normalized


class BatchTarget(StrictModel):
    provider: str
    project: str = ""

    @field_validator("provider")
    @classmethod
    def _provider_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not _REF.fullmatch(normalized):
            raise ValueError("must be a provider name")
        return normalized


class BatchState(StrictModel):
    name: str
    via: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _state_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("via", mode="before")
    @classmethod
    def _state_path(cls, value: object) -> object:
        if value in (None, ""):
            return ()
        return value


class BatchDefaults(StrictModel):
    issue_type: str = Field(default="", alias="type")
    state: BatchState | None = None
    labels: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    priority: str = ""

    @field_validator("state", mode="before")
    @classmethod
    def _short_state(cls, value: object) -> object:
        return {"name": value} if isinstance(value, str) else value


class BatchIssue(StrictModel):
    ref: str
    title: str | None = None
    body: str | None = None
    issue_type: str = Field(default="", alias="type")
    state: BatchState | None = None
    parent_ref: str = Field(default="", alias="parent")
    priority: str = ""
    labels: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    due_date: date | None = Field(default=None, alias="due")
    assignee: str = ""
    sources: dict[str, FieldSource] = Field(default_factory=dict)

    @field_validator("ref", "parent_ref")
    @classmethod
    def _valid_ref(cls, value: str) -> str:
        normalized = value.strip()
        if normalized and not _REF.fullmatch(normalized):
            raise ValueError("must be 1-64 letters, numbers, dots, dashes, or underscores")
        return normalized

    @field_validator("title")
    @classmethod
    def _one_line_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("state", mode="before")
    @classmethod
    def _short_state(cls, value: object) -> object:
        return {"name": value} if isinstance(value, str) else value

    @field_validator("sources")
    @classmethod
    def _known_sources(cls, value: dict[str, FieldSource]) -> dict[str, FieldSource]:
        allowed = {
            "title",
            "body",
            "type",
            "state",
            "parent",
            "priority",
            "labels",
            "components",
            "due",
            "assignee",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown provenance fields: {', '.join(unknown)}")
        return value


class BatchManifest(StrictModel):
    api_version: Literal["pykantui.dev/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["IssueBatch"]
    metadata: BatchMetadata
    target: BatchTarget
    defaults: BatchDefaults = Field(default_factory=BatchDefaults)
    issues: tuple[BatchIssue, ...]

    @model_validator(mode="after")
    def _valid_graph(self) -> BatchManifest:
        if len(self.issues) > MAX_BATCH_ISSUES:
            raise ValueError(f"a batch may contain at most {MAX_BATCH_ISSUES} issues")
        refs = [issue.ref for issue in self.issues]
        duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
        if duplicates:
            raise ValueError(f"duplicate issue refs: {', '.join(duplicates)}")
        known = set(refs)
        for issue in self.issues:
            if issue.parent_ref == issue.ref:
                raise ValueError(f"{issue.ref}: an issue cannot be its own parent")
            if issue.parent_ref and issue.parent_ref not in known:
                raise ValueError(f"{issue.ref}: unknown parent ref {issue.parent_ref!r}")
        self.ordered_issues()
        return self

    def ordered_issues(self) -> list[BatchIssue]:
        """Stable parent-before-child order, rejecting dependency cycles."""
        by_ref = {issue.ref: issue for issue in self.issues}
        pending = list(self.issues)
        ordered: list[BatchIssue] = []
        completed: set[str] = set()
        while pending:
            ready = [item for item in pending if not item.parent_ref or item.parent_ref in completed]
            if not ready:
                cycle = ", ".join(item.ref for item in pending)
                raise ValueError(f"dependency cycle among: {cycle}")
            for item in ready:
                ordered.append(by_ref[item.ref])
                completed.add(item.ref)
                pending.remove(item)
        return ordered


class _ManifestLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _ManifestLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing batch manifest",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_ManifestLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def load_manifest(path: Path) -> BatchManifest:
    """Read a bounded strict manifest without YAML aliases or custom objects."""
    resolved = path.expanduser().resolve()
    try:
        size = resolved.stat().st_size
        if size > MAX_MANIFEST_BYTES:
            raise ProviderError(f"batch manifest exceeds {MAX_MANIFEST_BYTES} bytes")
        text = resolved.read_text(encoding="utf-8")
        if any(isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(text)):
            raise ProviderError("batch YAML aliases and anchors are not allowed")
        raw = yaml.load(text, Loader=_ManifestLoader)
        if not isinstance(raw, dict):
            raise ProviderError("batch manifest must be a YAML mapping")
        return BatchManifest.model_validate(raw)
    except ProviderError:
        raise
    except FileNotFoundError as error:
        raise ProviderError(f"batch manifest does not exist: {resolved}") from error
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError, TypeError, RecursionError) as error:
        message = str(getattr(error, "problem", "") or error).splitlines()[0]
        raise ProviderError(f"invalid batch manifest: {message}") from error


def write_generated_manifest(path: Path, *, provider: str, count: int, force: bool = False) -> None:
    """Generate explicit placeholders; no executable templates enter the file."""
    if count < 1 or count > MAX_BATCH_ISSUES:
        raise ProviderError(f"count must be between 1 and {MAX_BATCH_ISSUES}")
    target = path.expanduser().resolve()
    if target.exists() and not force:
        raise ProviderError(f"{target} already exists", hint="Pass --force to replace it.")
    width = max(2, len(str(count)))
    manifest = BatchManifest(
        apiVersion="pykantui.dev/v1alpha1",
        kind="IssueBatch",
        metadata=BatchMetadata(name=target.stem),
        target=BatchTarget(provider=provider),
        defaults=BatchDefaults(),
        issues=tuple(
            BatchIssue(
                ref=f"issue-{index:0{width}d}",
                title=None,
            )
            for index in range(1, count + 1)
        ),
    )
    # Keep the generated file intentionally explicit: these are the fields a
    # person or AI is expected to fill. Normal manifest rewrites below stay
    # compact, but a scaffold should not make someone memorize the schema.
    document: dict[str, Any] = {
        "apiVersion": manifest.api_version,
        "kind": manifest.kind,
        "metadata": {"name": manifest.metadata.name},
        "target": {"provider": manifest.target.provider, "project": ""},
        "defaults": {"type": "", "state": None},
        "issues": [
            {
                "ref": issue.ref,
                "title": None,
                "body": None,
                "type": "",
                "state": None,
                "parent": "",
            }
            for issue in manifest.issues
        ],
    }
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=10_000)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)


def write_manifest(path: Path, manifest: BatchManifest, *, force: bool = False) -> None:
    """Write a validated manifest as inert, human-reviewable YAML."""
    target = path.expanduser().resolve()
    if target.exists() and not force:
        raise ProviderError(f"{target} already exists", hint="Pass --force to replace it.")
    document: dict[str, Any] = manifest.model_dump(
        mode="json",
        by_alias=True,
        exclude_defaults=True,
        exclude_none=True,
    )
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=10_000)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)


def manifest_sha256(path: Path) -> str:
    import hashlib  # noqa: PLC0415 - kept beside the only call site

    return hashlib.sha256(path.expanduser().resolve().read_bytes()).hexdigest()


__all__ = [
    "API_VERSION",
    "BatchDefaults",
    "BatchIssue",
    "BatchManifest",
    "BatchMetadata",
    "BatchState",
    "BatchTarget",
    "FieldSource",
    "load_manifest",
    "manifest_sha256",
    "write_generated_manifest",
    "write_manifest",
]
