"""Read-only verification of an existing run-tagged provider batch."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pykantui.tracker.models import RemoteIssue
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify(
    workspace: Path,
    output: Path,
    *,
    run_tag: str,
    expected: int,
    match_drafts_by_title: bool = False,
    exact_local_ids_only: bool = False,
) -> Path:
    project = Project.load(workspace)
    local: dict[str, dict[str, str]] = {}
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        title = str(parsed.front.get("title", ""))
        if run_tag not in title:
            continue
        identity = str(parsed.front.get("id", ""))
        if identity.startswith("draft-") and not match_drafts_by_title:
            continue
        local_key = title if match_drafts_by_title else identity
        local[local_key] = {
            "title": title,
            "title_sha256": _hash(title),
            "body_sha256": _hash(parsed.source),
        }
    if len(local) != expected:
        raise RuntimeError(f"local canonical count {len(local)} != {expected}")

    provider = project.open()
    try:
        provider.refresh()
        listed = (
            [
                RemoteIssue(issue_id=identity, key=identity, title=str(values["title"]))
                for identity, values in local.items()
            ]
            if exact_local_ids_only
            else [item for item in provider.iter_issues(project.project_id) if run_tag in item.title]
        )
        if len(listed) != expected:
            raise RuntimeError(f"remote tagged count {len(listed)} != {expected}")
        cards: list[dict[str, object]] = []
        for item in listed:
            exact = provider.get_issue(project.project_id, item)
            if exact is None:
                raise RuntimeError(f"exact read returned nothing for {item.display_key()}")
            local_key = exact.title if match_drafts_by_title else exact.issue_id
            expected_local = local.get(local_key)
            if expected_local is None:
                raise RuntimeError(f"no local Markdown for {item.display_key()} ({exact.issue_id})")
            if exact.title != expected_local["title"]:
                raise RuntimeError(f"title mismatch for {item.display_key()}")
            cards.append(
                {
                    "remote_id": exact.issue_id,
                    "key": exact.display_key(),
                    "title_sha256": _hash(exact.title),
                    "body_sha256": _hash(exact.body),
                    "status": exact.status,
                }
            )
    finally:
        provider.close()

    result = {
        "schema": 1,
        "provider": project.provider,
        "project_id": project.project_id,
        "run_tag": run_tag,
        "created": expected,
        "direct_exact_reads": len(cards),
        "cards": sorted(cards, key=lambda card: str(card["remote_id"])),
        "verification": (
            "read-only-recovery-title-match" if match_drafts_by_title else "read-only-recovery"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--expected", type=int, default=19)
    parser.add_argument("--match-drafts-by-title", action="store_true")
    parser.add_argument("--exact-local-ids-only", action="store_true")
    args = parser.parse_args()
    print(
        verify(
            args.workspace.resolve(),
            args.output.resolve(),
            run_tag=args.run_tag,
            expected=args.expected,
            match_drafts_by_title=args.match_drafts_by_title,
            exact_local_ids_only=args.exact_local_ids_only,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
