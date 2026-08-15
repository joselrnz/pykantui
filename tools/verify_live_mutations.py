"""Read-only direct API verification for an already-attempted mutation batch."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from live_tui_mutation_sync import _changed_local, remote_stub
except ModuleNotFoundError:
    from tools.live_tui_mutation_sync import _changed_local, remote_stub
from pykantui.workspace.project import Project


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify(workspace: Path, output: Path, *, run_tag: str) -> Path:
    project = Project.load(workspace)
    local = _changed_local(workspace, project, run_tag)
    comment_body = f"[{run_tag}:{project.provider}] post-create comment from TUI"
    if any(int(item["comment_drafts"]) for item in local.values()):
        raise RuntimeError("confirmed comment draft still remains locally")
    provider = project.open()
    try:
        cards: list[dict[str, object]] = []
        comment_ids: list[str] = []
        for identity, expected in local.items():
            exact = provider.get_issue(
                project.project_id,
                remote_stub(project.provider, identity, expected["key"], expected["title"]),
            )
            if exact is None:
                raise RuntimeError(f"exact API read returned nothing for {expected['key']}")
            if exact.title != expected["title"] or exact.body.strip() != expected["body"].strip():
                raise RuntimeError(f"direct field mismatch for {expected['key']}")
            if expected["kind"] == "tui" and expected["status"] and exact.status != expected["status"]:
                raise RuntimeError(f"direct status mismatch for {expected['key']}")
            if expected["kind"] == "tui":
                matches = [
                    comment
                    for comment in provider.comments(project.project_id, exact, refresh=True)
                    if comment.body == comment_body
                ]
                if len(matches) != 1:
                    raise RuntimeError(f"direct comment matches {len(matches)} != 1")
                comment_ids = [comment.comment_id for comment in matches]
            cards.append(
                {
                    "remote_id": exact.issue_id,
                    "key": exact.display_key(),
                    "kind": expected["kind"],
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
        "updates": 2,
        "moves": 1,
        "comments": 1,
        "direct_exact_reads": len(cards),
        "comment_ids": comment_ids,
        "cards": sorted(cards, key=lambda card: str(card["remote_id"])),
        "verification": "read-only-recovery",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    args = parser.parse_args()
    print(verify(args.workspace.resolve(), args.output.resolve(), run_tag=args.run_tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
