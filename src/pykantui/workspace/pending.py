"""Durable records for provider creates whose outcome is uncertain."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pykantui.config.paths import write_text_atomic
from pykantui.tracker.errors import ProviderError

PENDING_CREATE_SCHEMA = 1
PENDING_COMMENT_SCHEMA = 1


class PendingCommentState(StrEnum):
    """Durable states for a non-idempotent comment attempt."""

    ATTEMPTING = "attempting"
    CONFIRMED = "confirmed"


class PendingCreate(BaseModel):
    """One draft whose create request may already have succeeded remotely."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str
    signature: str
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PendingCreateJournal(BaseModel):
    """Atomic workspace journal preventing accidental duplicate creates."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schema")
    attempts: dict[str, PendingCreate] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> PendingCreateJournal:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise ProviderError(
                "pending create journal is unreadable; refusing to create cards",
                hint="Restore or inspect .pykantui/pending-creates.json before retrying.",
            ) from error

    def begin(self, path: Path, draft_id: str, *, filename: str, signature: str) -> None:
        self.attempts[draft_id] = PendingCreate(filename=filename, signature=signature)
        self.save(path)

    def resolve(self, path: Path, draft_id: str) -> None:
        self.attempts.pop(draft_id, None)
        if self.attempts:
            self.save(path)
        else:
            path.unlink(missing_ok=True)

    def save(self, path: Path) -> None:
        write_text_atomic(path, self.model_dump_json(indent=2, by_alias=True))


class PendingComment(BaseModel):
    """One comment POST protected against an accidental replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_id: str
    filename: str
    signature: str
    state: PendingCommentState = PendingCommentState.ATTEMPTING
    remote_id: str = ""
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _confirmed_attempt_has_a_remote_id(self) -> PendingComment:
        if self.state is PendingCommentState.CONFIRMED and not self.remote_id.strip():
            raise ValueError("a confirmed comment attempt requires a remote id")
        return self


class PendingCommentJournal(BaseModel):
    """Atomic journal for non-idempotent append-only comment POSTs."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schema")
    attempts: dict[str, PendingComment] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> PendingCommentJournal:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise ProviderError(
                "pending comment journal is unreadable; refusing to create comments",
                hint="Restore or inspect .pykantui/pending-comments.json before retrying.",
            ) from error

    def begin(
        self,
        path: Path,
        local_id: str,
        *,
        issue_id: str,
        filename: str,
        signature: str,
    ) -> None:
        self.attempts[local_id] = PendingComment(
            issue_id=issue_id,
            filename=filename,
            signature=signature,
        )
        self.save(path)

    def confirm(self, path: Path, local_id: str, *, remote_id: str) -> None:
        attempt = self.attempts[local_id]
        self.attempts[local_id] = PendingComment.model_validate(
            {
                **attempt.model_dump(),
                "state": PendingCommentState.CONFIRMED,
                "remote_id": remote_id,
            }
        )
        self.save(path)

    def resolve(self, path: Path, local_id: str) -> None:
        self.attempts.pop(local_id, None)
        if self.attempts:
            self.save(path)
        else:
            path.unlink(missing_ok=True)

    def save(self, path: Path) -> None:
        write_text_atomic(path, self.model_dump_json(indent=2, by_alias=True))


__all__ = [
    "PendingComment",
    "PendingCommentJournal",
    "PendingCommentState",
    "PendingCreate",
    "PendingCreateJournal",
]
