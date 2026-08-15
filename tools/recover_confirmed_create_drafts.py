"""Recover locally retained drafts after independently confirmed provider creates.

This tool never contacts a provider. It trusts only a previously captured,
hash-pinned one-to-one draft/canonical manifest, preserves local notes and
discussion on the canonical file, then moves each draft to a recoverable
quarantine outside the active workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pykantui.config.paths import write_text_atomic
from pykantui.tracker.models import CommentDraft, RemoteComment, RemoteIssue
from pykantui.tracker.util import parse_date
from pykantui.workspace import layout, markdown
from pykantui.workspace.state import SyncState


class RecoveryError(RuntimeError):
    """The local recovery proof is incomplete or changed underneath us."""


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    validated_pairs: int
    pending_comments: int
    canonical_rewritten: int
    quarantined: int
    executed: bool


@dataclass(frozen=True, slots=True)
class _RecoveryPair:
    draft_path: Path
    canonical_path: Path
    destination: Path
    canonical_text: str
    draft_hash: str
    canonical_hash: str
    rewrite_canonical: bool
    pending_comments: int


def recover(
    workspace: Path,
    quarantine: Path,
    manifest_path: Path,
    *,
    run_tag: str,
    execute: bool,
) -> RecoveryResult:
    """Validate every byte first, then merge and quarantine without deletion."""
    workspace = workspace.resolve(strict=True)
    quarantine = quarantine.resolve(strict=False)
    manifest_path = manifest_path.resolve(strict=True)
    _within(workspace.parent, quarantine, label="quarantine")
    if quarantine == workspace or workspace in quarantine.parents:
        raise RecoveryError("quarantine must be outside the active workspace")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecoveryError(f"cannot read recovery manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise RecoveryError("recovery manifest must be a JSON object")
    if manifest.get("schema") != 1 or manifest.get("capture") != "read-only-before-local-repair":
        raise RecoveryError("recovery manifest has the wrong schema or capture type")
    if manifest.get("run_tag") != run_tag:
        raise RecoveryError("recovery manifest run tag does not match")
    provider = str(manifest.get("provider") or "")
    if not provider:
        raise RecoveryError("recovery manifest has no provider")
    pairs_raw = manifest.get("pairs")
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise RecoveryError("recovery manifest has no pairs")
    expected = len(pairs_raw)
    for field_name in ("active_drafts", "canonical_cards", "exact_title_pairs"):
        if manifest.get(field_name) != expected:
            raise RecoveryError(f"recovery manifest {field_name} does not match its pairs")

    state = SyncState.load(layout.state_file(workspace))
    prepared: list[_RecoveryPair] = []
    seen_sources: set[Path] = set()
    seen_canonical: set[Path] = set()
    seen_destinations: set[Path] = set()
    for index, pair_raw in enumerate(pairs_raw, start=1):
        if not isinstance(pair_raw, dict):
            raise RecoveryError(f"pair {index} is not an object")
        draft_record = _record(pair_raw.get("draft"), index, "draft")
        canonical_record = _record(pair_raw.get("canonical"), index, "canonical")
        draft_path = _manifest_file(workspace, draft_record, index, "draft")
        canonical_path = _manifest_file(workspace, canonical_record, index, "canonical")
        if draft_path in seen_sources or canonical_path in seen_canonical:
            raise RecoveryError(f"pair {index} repeats a source or canonical path")
        seen_sources.add(draft_path)
        seen_canonical.add(canonical_path)

        draft_file = markdown.read(draft_path)
        canonical_file = markdown.read(canonical_path)
        if not draft_file.valid or not canonical_file.valid:
            raise RecoveryError(f"pair {index} contains invalid Markdown")
        draft_id = str(draft_file.front.get("id") or "")
        canonical_id = str(canonical_file.front.get("id") or "")
        if draft_id != draft_record["id"] or not draft_id.startswith("draft-"):
            raise RecoveryError(f"pair {index} draft identity changed")
        if canonical_id != canonical_record["id"] or canonical_id.startswith("draft-"):
            raise RecoveryError(f"pair {index} canonical identity changed")
        draft_title = str(draft_file.front.get("title") or "")
        canonical_title = str(canonical_file.front.get("title") or "")
        if run_tag not in draft_title or draft_title != canonical_title:
            raise RecoveryError(f"pair {index} titles are not exact run-owned matches")
        title_hash = _sha256(draft_title.encode())
        if title_hash != draft_record["title_sha256"] or title_hash != canonical_record["title_sha256"]:
            raise RecoveryError(f"pair {index} title hash changed")
        if str(draft_file.front.get("provider") or "") != provider:
            raise RecoveryError(f"pair {index} draft provider changed")
        if str(canonical_file.front.get("provider") or "") != provider:
            raise RecoveryError(f"pair {index} canonical provider changed")

        baseline = state.get(canonical_id)
        if baseline is None or baseline.title != canonical_title:
            raise RecoveryError(f"pair {index} has no matching canonical sync snapshot")
        issue = _canonical_issue(canonical_file, baseline)
        comments = _merge_comments(canonical_file.comments, draft_file.comments, canonical_id, index)
        comment_drafts = _merge_comment_drafts(
            canonical_file.comment_drafts,
            draft_file.comment_drafts,
            canonical_id,
            index,
        )
        if canonical_file.notes and draft_file.notes and canonical_file.notes != draft_file.notes:
            raise RecoveryError(f"pair {index} has conflicting private notes")
        notes = canonical_file.notes or draft_file.notes
        canonical_text = markdown.render(
            issue,
            column_name=str(canonical_file.front.get("column") or canonical_path.parent.name),
            notes=notes,
            provider=provider,
            comments=comments,
            comment_drafts=comment_drafts,
            include_comment_region=bool(
                canonical_file.has_comment_region
                or draft_file.has_comment_region
                or comments
                or comment_drafts
            ),
        )
        relative = draft_path.relative_to(workspace)
        destination = (quarantine / relative).resolve(strict=False)
        _within(quarantine, destination, label=f"pair {index} destination")
        if destination in seen_destinations or destination.exists():
            raise RecoveryError(f"pair {index} quarantine destination already exists")
        seen_destinations.add(destination)
        prepared.append(
            _RecoveryPair(
                draft_path=draft_path,
                canonical_path=canonical_path,
                destination=destination,
                canonical_text=canonical_text,
                draft_hash=draft_record["file_sha256"],
                canonical_hash=canonical_record["file_sha256"],
                rewrite_canonical=(
                    canonical_text != canonical_path.read_text(encoding="utf-8")
                ),
                pending_comments=len(draft_file.comment_drafts),
            )
        )

    pending_comments = sum(pair.pending_comments for pair in prepared)
    rewritten = sum(pair.rewrite_canonical for pair in prepared)
    if execute:
        for pair in prepared:
            # Re-check both hashes immediately before the first mutation. The
            # complete validation above means no file moves on a stale proof.
            if _sha256(pair.draft_path.read_bytes()) != pair.draft_hash:
                raise RecoveryError("draft file hash changed during recovery")
            if _sha256(pair.canonical_path.read_bytes()) != pair.canonical_hash:
                raise RecoveryError("canonical file hash changed during recovery")
            if pair.rewrite_canonical:
                write_text_atomic(pair.canonical_path, pair.canonical_text)
            pair.destination.parent.mkdir(parents=True, exist_ok=True)
            pair.draft_path.replace(pair.destination)
    return RecoveryResult(
        validated_pairs=len(prepared),
        pending_comments=pending_comments,
        canonical_rewritten=rewritten,
        quarantined=len(prepared) if execute else 0,
        executed=execute,
    )


def _record(value: object, index: int, kind: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RecoveryError(f"pair {index} has no {kind} record")
    required = ("file", "id", "title_sha256", "file_sha256")
    record = {name: str(value.get(name) or "") for name in required}
    if any(not record[name] for name in required):
        raise RecoveryError(f"pair {index} {kind} record is incomplete")
    return record


def _manifest_file(
    workspace: Path,
    record: dict[str, str],
    index: int,
    kind: str,
) -> Path:
    candidate = (workspace / record["file"]).resolve(strict=True)
    _within(workspace, candidate, label=f"pair {index} {kind}")
    if candidate.is_symlink() or not candidate.is_file():
        raise RecoveryError(f"pair {index} {kind} is not a regular file")
    if _sha256(candidate.read_bytes()) != record["file_sha256"]:
        raise RecoveryError(f"pair {index} {kind} file hash changed")
    return candidate


def _within(root: Path, candidate: Path, *, label: str) -> None:
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as error:
        raise RecoveryError(f"{label} escapes its allowed root") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_issue(parsed: markdown.IssueFile, baseline: RemoteIssue) -> RemoteIssue:
    front = parsed.front
    labels = front.get("labels")
    components = front.get("components")
    return baseline.model_copy(
        update={
            "title": str(front.get("title") or baseline.title),
            "status": str(front.get("status") or baseline.status),
            "body": parsed.source,
            "issue_type": str(front.get("type") or ""),
            "priority": str(front.get("priority") or ""),
            "assignee": str(front.get("assignee") or ""),
            "labels": tuple(str(item) for item in labels) if isinstance(labels, list) else (),
            "components": (
                tuple(str(item) for item in components) if isinstance(components, list) else ()
            ),
            "parent_key": str(front.get("parent") or ""),
            "due_date": parse_date(front.get("due")),
            "url": str(front.get("url") or baseline.url),
        }
    )


def _merge_comments(
    canonical: tuple[RemoteComment, ...],
    retained: tuple[RemoteComment, ...],
    issue_id: str,
    index: int,
) -> tuple[RemoteComment, ...]:
    found = {comment.comment_id: comment for comment in canonical}
    for comment in retained:
        normalized = comment.model_copy(update={"issue_id": issue_id})
        previous = found.get(comment.comment_id)
        if previous is not None and previous.model_dump() != normalized.model_dump():
            raise RecoveryError(f"pair {index} has conflicting provider comment ids")
        found[comment.comment_id] = normalized
    return tuple(found[key] for key in sorted(found))


def _merge_comment_drafts(
    canonical: tuple[CommentDraft, ...],
    retained: tuple[CommentDraft, ...],
    issue_id: str,
    index: int,
) -> tuple[CommentDraft, ...]:
    found = {
        draft.local_id: draft.model_copy(update={"issue_id": issue_id}) for draft in canonical
    }
    for draft in retained:
        normalized = draft.model_copy(update={"issue_id": issue_id})
        previous = found.get(draft.local_id)
        if previous is not None and previous.model_dump() != normalized.model_dump():
            raise RecoveryError(f"pair {index} has conflicting pending comment ids")
        found[draft.local_id] = normalized
    return tuple(found[key] for key in sorted(found))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = recover(
        args.workspace,
        args.quarantine,
        args.manifest,
        run_tag=args.run_tag,
        execute=args.execute,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
