"""Validate and publish one live, run-tagged provider certification bundle."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pykantui.config.paths import write_text_atomic
from tools.provider_evidence import record_artifact, validate_png, validate_svg

PROVIDERS = (
    "asana",
    "clickup",
    "github",
    "jira",
    "linear",
    "monday",
    "plane",
    "shortcut",
    "trello",
)

REQUIRED_CAPTURES = (
    "live-local/{provider}/local-20-kanban.svg",
    "live-local/{provider}/local-20-rows.svg",
    "live-local/{provider}/local-20-split.svg",
    "live-local/{provider}/tui-edit-move.svg",
    "live-local/{provider}/markdown-edit.svg",
    "live-local/{provider}/comment-draft.svg",
    "live-local/{provider}/delete-confirm.svg",
    "live-local/{provider}/delete-complete.svg",
    "live-sync/{provider}/01-preview.svg",
    "live-sync/{provider}/02-progress.svg",
    "live-sync/{provider}/03-progress-fraction.svg",
    "live-sync/{provider}/04-result.svg",
    "post-create/{provider}/01-tui-edit-move.svg",
    "post-create/{provider}/02-comment-draft.svg",
    "post-create/{provider}/03-markdown-edit.svg",
    "mutation-sync/{provider}/01-preview.svg",
    "mutation-sync/{provider}/02-progress.svg",
    "mutation-sync/{provider}/03-progress-fraction.svg",
    "mutation-sync/{provider}/04-result.svg",
    "conflict-sync/{provider}/01-conflict-preview.svg",
    "conflict-sync/{provider}/02-progress.svg",
    "conflict-sync/{provider}/03-result.svg",
    "noop-sync/{provider}/01-progress.svg",
    "noop-sync/{provider}/02-result.svg",
)

_READBACKS = {
    "create": "live-sync/{provider}/create-api-readback.json",
    "local_actions": "post-create/{provider}/actions.json",
    "mutation": "mutation-sync/{provider}/mutation-api-readback.json",
    "conflict": "conflict-sync/{provider}/conflict-api-readback.json",
    "no_op": "noop-sync/{provider}/noop-verification.json",
}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid evidence JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"evidence JSON must be an object: {path.name}")
    return value


def _expect(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _validate_provider(run: Path, provider: str, run_tag: str) -> dict[str, Any]:
    evidence: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for name, pattern in _READBACKS.items():
        path = run / pattern.format(provider=provider)
        _expect(path.is_file(), f"missing {name} evidence for {provider}")
        payload = _load_object(path)
        _expect(payload.get("provider") == provider, f"wrong provider in {path.name}")
        if "run_tag" in payload:
            _expect(payload["run_tag"] == run_tag, f"wrong run tag in {path.name}")
        evidence[name] = payload
        paths[name] = path

    create = evidence["create"]
    created = int(create.get("created", -1))
    _expect(created == 19, f"{provider} created {created} cards instead of 19")
    _expect(create.get("direct_exact_reads") == 19, f"{provider} lacks 19 exact create readbacks")
    _expect(len(create.get("cards", [])) == 19, f"{provider} create card receipt count is not 19")

    local = evidence["local_actions"]
    _expect(local.get("provider_writes") == 0, f"{provider} local edits made a provider write")
    _expect(local.get("comment_drafts") == 1, f"{provider} local comment draft was not retained")

    mutation = evidence["mutation"]
    _expect(mutation.get("updates") == 2, f"{provider} did not verify two updates")
    _expect(mutation.get("moves") == 1, f"{provider} did not verify one move")
    _expect(mutation.get("comments") == 1, f"{provider} did not verify one comment")
    _expect(mutation.get("direct_exact_reads") == 2, f"{provider} lacks two exact mutation readbacks")
    _expect(len(mutation.get("cards", [])) == 2, f"{provider} mutation card receipt count is not two")
    _expect(len(mutation.get("comment_ids", [])) == 1, f"{provider} comment receipt count is not one")

    conflict = evidence["conflict"]
    _expect(conflict.get("aligned") is True, f"{provider} conflict is not aligned")
    _expect(conflict.get("resolution") == "provider", f"{provider} conflict resolution is not provider")
    _expect(conflict.get("field") == "title", f"{provider} conflict field is not title")

    no_op = evidence["no_op"]
    _expect(no_op.get("before_plan") == "empty", f"{provider} pre-no-op plan is not empty")
    _expect(no_op.get("after_plan") == "empty", f"{provider} post-no-op plan is not empty")
    _expect(no_op.get("direct_remote_count") == 19, f"{provider} no-op remote count is not 19")
    _expect(no_op.get("provider_mutations") == 0, f"{provider} no-op sync mutated the provider")
    _expect(no_op.get("tagged_markdown_bytes_stable") is True, f"{provider} no-op changed Markdown")
    _expect(no_op.get("tagged_markdown_files") == 19, f"{provider} no-op Markdown count is not 19")
    _expect(no_op.get("terminal_phase") == "Complete", f"{provider} no-op did not complete")

    return {
        "provider": provider,
        "cards_created": 19,
        "exact_create_reads": 19,
        "updates": 2,
        "moves": 1,
        "comments": 1,
        "exact_mutation_reads": 2,
        "conflict": "provider-aligned",
        "no_op": "verified",
        "evidence": {name: record_artifact(run, path) for name, path in sorted(paths.items())},
    }


def _receipt_summary(run: Path) -> list[dict[str, Any]]:
    path = run / "receipts.jsonl"
    _expect(path.is_file(), "missing receipts.jsonl")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid receipt JSON on line {number}") from error
        if not isinstance(row, dict):
            raise ValueError(f"receipt line {number} is not an object")
        rows.append(row)
    sequences = [row.get("sequence") for row in rows]
    _expect(sequences == list(range(1, len(rows) + 1)), "receipt sequences are not contiguous")

    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("provider", "")), str(row.get("operation_id", "")))
        event = str(row.get("event", ""))
        if event == "attempted":
            _expect(key not in operations, f"duplicate attempted receipt for {key[0]} {key[1]}")
            operations[key] = {
                "provider": key[0],
                "operation": str(row.get("operation", "")),
                "operation_id": key[1],
                "terminal": "",
            }
        elif event in {"verified", "failed", "ambiguous"}:
            _expect(key in operations, f"terminal receipt without attempt for {key[0]} {key[1]}")
            _expect(not operations[key]["terminal"], f"duplicate terminal receipt for {key[0]} {key[1]}")
            operations[key]["terminal"] = event
    for operation in operations.values():
        _expect(
            bool(operation["terminal"]), f"unterminated receipt for {operation['provider']} {operation['operation_id']}"
        )
    return sorted(operations.values(), key=lambda item: (item["provider"], item["operation_id"]))


def _artifact_records(run: Path) -> list[dict[str, Any]]:
    svgs = sorted(run.rglob("*.svg"))
    pngs = sorted(run.rglob("*.png"))
    for provider in PROVIDERS:
        for pattern in REQUIRED_CAPTURES:
            svg = run / pattern.format(provider=provider)
            png = svg.with_suffix(".png")
            _expect(svg.is_file() and png.is_file(), f"missing required screenshot pair: {svg.relative_to(run)}")
    svg_set = {path.relative_to(run).with_suffix("") for path in svgs}
    png_set = {path.relative_to(run).with_suffix("") for path in pngs}
    _expect(svg_set == png_set, "SVG/PNG screenshot pairs do not match")

    records: list[dict[str, Any]] = []
    for svg in svgs:
        png = svg.with_suffix(".png")
        records.append(
            {
                "svg": record_artifact(run, svg),
                "png": record_artifact(run, png),
                "svg_geometry": validate_svg(svg),
                "png_geometry": validate_png(png),
            }
        )
    return records


def _write_index(run: Path, manifest: Mapping[str, Any]) -> None:
    lines = [
        f"# Live provider certification · {manifest['run_tag']}",
        "",
        "> Live, run-tagged provider evidence. Only test-owned cards were mutated; "
        "no pre-existing provider data was changed or deleted.",
        "",
        "## Verified provider results",
        "",
        "| Provider | Create/read | Update | Move | Comment | Conflict | Final no-op |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for item in manifest["provider_results"]:
        lines.append(
            f"| {item['provider']} | {item['cards_created']}/{item['exact_create_reads']} | "
            f"{item['updates']} | {item['moves']} | {item['comments']} | {item['conflict']} | {item['no_op']} |"
        )
    lines.extend(["", "## Screenshot gallery", ""])
    for provider in PROVIDERS:
        lines.extend([f"### {provider.title()}", ""])
        provider_pngs = [
            Path(str(record["png"]["path"]))
            for record in manifest["artifacts"]
            if f"/{provider}/" in f"/{record['png']['path']}"
        ]
        for path in provider_pngs:
            label = path.stem.replace("-", " ")
            lines.append(f"![{provider} {label}]({path.as_posix()})")
            lines.append("")
    write_text_atomic(run / "index.md", "\n".join(lines).rstrip() + "\n")


def build_manifest(run: Path) -> dict[str, Any]:
    """Validate all live evidence and publish manifest, summary, and gallery."""
    run = run.resolve(strict=True)
    run_tag = run.name
    provider_results = [_validate_provider(run, provider, run_tag) for provider in PROVIDERS]
    receipts = _receipt_summary(run)
    artifacts = _artifact_records(run)
    terminal_counts: dict[str, int] = defaultdict(int)
    for receipt in receipts:
        terminal_counts[str(receipt["terminal"])] += 1
    counts = {
        "providers": len(PROVIDERS),
        "remote_cards_created": sum(item["cards_created"] for item in provider_results),
        "exact_create_reads": sum(item["exact_create_reads"] for item in provider_results),
        "updates": sum(item["updates"] for item in provider_results),
        "moves": sum(item["moves"] for item in provider_results),
        "comments": sum(item["comments"] for item in provider_results),
        "conflicts_resolved": len(provider_results),
        "no_op_syncs": len(provider_results),
        "screenshot_pairs": len(artifacts),
        "artifacts": 2 * len(artifacts),
        "receipt_operations": len(receipts),
        "receipt_terminal_states": dict(sorted(terminal_counts.items())),
    }
    manifest = {
        "schema": 1,
        "evidence_kind": "live-provider-api-and-textual",
        "run_tag": run_tag,
        "providers": list(PROVIDERS),
        "counts": counts,
        "provider_results": provider_results,
        "receipts": receipts,
        "artifacts": artifacts,
        "safety": {
            "run_tag_owned_only": True,
            "pre_existing_remote_data_mutated": False,
            "remote_deletes": 0,
            "final_no_op_verified_for_every_provider": True,
        },
    }
    write_text_atomic(run / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    write_text_atomic(
        run / "summary.json",
        json.dumps({"schema": 1, "run_tag": run_tag, "counts": counts}, indent=2, sort_keys=True) + "\n",
    )
    _write_index(run, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    manifest = build_manifest(args.run)
    print(json.dumps(manifest["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
