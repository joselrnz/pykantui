"""Generate a bounded batch of provider-aware local Markdown drafts.

This command never calls a provider write method. It reads the configured
provider's columns and type catalogue once, then renders every draft through
the same canonical Markdown writer used by ``kbn new``.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

from pykantui.commands.new import write_draft
from pykantui.tracker import ProviderError
from pykantui.tracker.models import IssueDraft
from pykantui.workspace import layout, markdown
from pykantui.workspace.project import Project

_RUN_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def validate_run_tag(value: str) -> str:
    """Return a terminal/path-safe run tag or fail before provider access."""

    if not _RUN_TAG.fullmatch(value):
        raise ValueError("run tag must contain 1-80 letters, numbers, dots, underscores, or dashes")
    return value


def draft_numbers(*, existing: int, wanted: int) -> tuple[int, ...]:
    """Return the exact missing 1-based ordinals for an idempotent batch."""

    if not 1 <= wanted <= 100:
        raise ValueError("count must be between 1 and 100")
    if existing < 0 or existing > wanted:
        raise ValueError(f"existing draft count {existing} is outside the requested batch of {wanted}")
    return tuple(range(existing + 1, wanted + 1))


def generate(
    workspace: Path,
    *,
    run_tag: str,
    count: int,
    column_name: str,
    issue_type: str = "",
    body: str = "",
    due_date: date | None = None,
) -> tuple[Path, ...]:
    """Create only missing owned drafts and validate every rendered file."""

    tag = validate_run_tag(run_tag)
    workspace = workspace.resolve(strict=True)
    project = Project.load(workspace)
    marker = f"[{tag}:{project.provider}] card "
    existing_paths = []
    for path in layout.iter_issue_files(workspace, project.provider, project.remote()):
        parsed = markdown.read(path)
        if str(parsed.front.get("title", "")).startswith(marker):
            if not str(parsed.front.get("id", "")).startswith("draft-"):
                raise ProviderError("run marker already belongs to a synced card; refusing local generation")
            existing_paths.append(path)

    ordinals = draft_numbers(existing=len(existing_paths), wanted=count)
    made: list[Path] = []
    provider = project.open()
    with provider:
        columns = provider.columns(project.project_id)
        column = next((item for item in columns if item.name.casefold() == column_name.casefold()), None)
        if column is None:
            offered = ", ".join(item.name for item in columns)
            raise ProviderError(f"column {column_name!r} not found", hint=f"Available: {offered}")
        resolved_type = provider.resolve_issue_type(project.project_id, issue_type)
        creatable = set(provider.creatable_card_fields())
        if body and "body" not in creatable:
            raise ProviderError(f"{provider.spec.label} does not expose a body field in this project")
        if due_date and "due_date" not in creatable:
            raise ProviderError(f"{provider.spec.label} does not expose a due-date field in this project")
        for number in ordinals:
            draft = IssueDraft(
                title=f"{marker}{number:02d}",
                body=body,
                issue_type=resolved_type.name if resolved_type else "",
                column_id=column.column_id,
                column_name=column.name,
                due_date=due_date,
            )
            path = write_draft(workspace, project, column, draft)
            parsed = markdown.read(path)
            if not parsed.valid or parsed.front.get("title") != draft.title:
                raise ProviderError(f"generated Markdown did not validate: {path.name}")
            made.append(path)
    return tuple(made)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--column", required=True)
    parser.add_argument("--type", dest="issue_type", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--due", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    try:
        made = generate(
            args.workspace,
            run_tag=args.run_tag,
            count=args.count,
            column_name=args.column,
            issue_type=args.issue_type,
            body=args.body,
            due_date=args.due,
        )
    except (ProviderError, ValueError, OSError) as error:
        print(f"error: {error}")
        return 2
    print(f"generated {len(made)} local Markdown draft(s); nothing sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
