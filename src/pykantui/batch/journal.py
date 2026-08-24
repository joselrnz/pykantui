"""Durable per-item phases for resumable non-idempotent batch writes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pykantui.config.paths import write_text_atomic
from pykantui.tracker.errors import ProviderError
from pykantui.tracker.models import RemoteIssue


class BatchApplyPhase(StrEnum):
    READY = "ready"
    CREATING = "creating"
    CREATED = "created"
    TRANSITIONING = "transitioning"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class BatchApplyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signature: str
    phase: BatchApplyPhase = BatchApplyPhase.READY
    remote_issue: RemoteIssue | None = None
    next_transition: int = 0
    transition_column_id: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _confirmed_has_remote_issue(self) -> BatchApplyItem:
        if self.phase in {
            BatchApplyPhase.CREATED,
            BatchApplyPhase.TRANSITIONING,
            BatchApplyPhase.COMPLETE,
        } and self.remote_issue is None:
            raise ValueError(f"{self.phase} item needs the confirmed remote issue")
        return self


class BatchApplyJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schema")
    batch_id: str
    plan_hash: str
    items: dict[str, BatchApplyItem] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path, *, batch_id: str, plan_hash: str) -> BatchApplyJournal:
        try:
            if path.stat().st_size > 2_097_152:
                raise ProviderError("batch apply journal is too large; refusing provider writes")
            journal = cls.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(batch_id=batch_id, plan_hash=plan_hash)
        except ProviderError:
            raise
        except (OSError, ValidationError, ValueError) as error:
            raise ProviderError("batch apply journal is unreadable; refusing provider writes") from error
        if journal.batch_id != batch_id:
            raise ProviderError("batch apply journal belongs to a different batch")
        if journal.plan_hash != plan_hash:
            raise ProviderError(
                "batch already has state from a different plan",
                hint="Review the existing batch state before changing an applied batch.",
            )
        return journal

    def begin_create(self, path: Path, ref: str, *, signature: str) -> None:
        self.items[ref] = BatchApplyItem(signature=signature, phase=BatchApplyPhase.CREATING)
        self.save(path)

    def confirm_create(self, path: Path, ref: str, issue: RemoteIssue) -> None:
        current = self.items[ref]
        self.items[ref] = current.model_copy(
            update={
                "phase": BatchApplyPhase.CREATED,
                "remote_issue": issue,
                "updated_at": datetime.now(UTC),
            }
        )
        self.save(path)

    def begin_transition(self, path: Path, ref: str, *, hop: int, column_id: str) -> None:
        current = self.items[ref]
        self.items[ref] = current.model_copy(
            update={
                "phase": BatchApplyPhase.TRANSITIONING,
                "next_transition": hop,
                "transition_column_id": column_id,
                "updated_at": datetime.now(UTC),
            }
        )
        self.save(path)

    def confirm_transition(
        self,
        path: Path,
        ref: str,
        *,
        hop: int,
        issue: RemoteIssue,
        complete: bool,
    ) -> None:
        current = self.items[ref]
        self.items[ref] = current.model_copy(
            update={
                "phase": BatchApplyPhase.COMPLETE if complete else BatchApplyPhase.CREATED,
                "remote_issue": issue,
                "next_transition": hop + 1,
                "transition_column_id": "",
                "updated_at": datetime.now(UTC),
            }
        )
        self.save(path)

    def complete(self, path: Path, ref: str) -> None:
        current = self.items[ref]
        self.items[ref] = current.model_copy(
            update={"phase": BatchApplyPhase.COMPLETE, "updated_at": datetime.now(UTC)}
        )
        self.save(path)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(self.model_dump(mode="json", by_alias=True), indent=2))


__all__ = ["BatchApplyItem", "BatchApplyJournal", "BatchApplyPhase"]
