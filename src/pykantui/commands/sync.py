"""``kbn sync`` — send your edits, then pull what changed.

The order is the one the design rests on: local edits go first, because they
exist nowhere else, and the pull afterwards is written from what the tracker
actually accepted.

Nothing is sent without a yes. On a terminal that is a prompt; with ``--yes``
it is assumed; with ``--dry-run`` the answer is always no and you get the plan
without the consequences.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pykantui.commands.new import is_draft
from pykantui.i18n import translate as _
from pykantui.tracker import ProviderError
from pykantui.tracker.mine import Scope
from pykantui.workspace import layout
from pykantui.workspace import sync as sync_module
from pykantui.workspace.project import Project
from pykantui.workspace.sync import ConfirmPush, SyncPlan, SyncReport


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("reconcile a workspace with its provider")
    parser = sub.add_parser("sync", help=summary, description=summary)
    parser.add_argument("--path", type=Path, default=None, help=_("the workspace (default: found by walking up)"))
    parser.add_argument("--yes", action="store_true", help=_("do not ask before sending changes to the provider"))
    parser.add_argument("--dry-run", action="store_true", help=_("show what would be sent, and send nothing"))
    parser.add_argument("--pull-only", action="store_true", help=_("never send, whatever changed locally"))
    parser.add_argument("--refresh", action="store_true", help=_("ignore the cached issue list"))
    parser.add_argument(
        "--mine",
        action="store_true",
        help=_("write only issues assigned to or reported by you (the cache still holds all)"),
    )
    parser.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help=_("mirror the whole project, whatever the workspace is set to"),
    )
    conflict_action = parser.add_mutually_exclusive_group()
    conflict_action.add_argument(
        "--force",
        action="store_true",
        help=_("send even where the provider also changed the issue, overwriting it"),
    )
    conflict_action.add_argument(
        "--accept-provider-conflicts",
        action="store_true",
        help=_("use provider values for conflicts while sending safe local changes"),
    )
    parser.add_argument(
        "--retry-ambiguous-creates",
        action="store_true",
        help=_("retry draft creates whose previous outcome was unknown, after checking the provider"),
    )
    parser.add_argument(
        "--retry-ambiguous-comments",
        action="store_true",
        help=_("retry comment creates whose previous outcome was unknown; this may create a duplicate"),
    )
    parser.add_argument("--no-commit", dest="commit", action="store_false", help=_("do not commit the result"))


def run(args: argparse.Namespace) -> int:
    try:
        workspace = _find(args.path)
        project = Project.load(workspace)

        with project.open() as provider:
            if args.refresh:
                provider.refresh()

            scope = _scope_for(args, project)
            identity = None if scope is None else project.model_copy(update={"scope": scope}).identity(provider)

            report = sync_module.sync(
                workspace,
                provider,
                project.remote(),
                identity=identity,
                scope=scope,
                push_edits=not args.pull_only,
                commit=args.commit,
                column_style=project.column_style,
                confirm=_confirm_for(args),
                push_conflicts=args.force,
                accept_remote_conflicts=args.accept_provider_conflicts,
                retry_ambiguous_creates=args.retry_ambiguous_creates,
                retry_ambiguous_comments=args.retry_ambiguous_comments,
            )
    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled", file=sys.stderr)
        return 130

    _report(report)
    return 0


def _scope_for(args: argparse.Namespace, project: Project) -> Scope | None:
    """Which issues this run should write.

    The flags are one-shot overrides and are not written back: turning a
    workspace personal is a decision about the workspace, made at ``kbn init``
    or by editing ``project.json``, not a side effect of one sync.
    """
    if args.everything:
        return None
    if args.mine:
        return Scope()
    return project.scope


def _find(supplied: Path | None) -> Path:
    if supplied is not None:
        return supplied.expanduser().resolve()
    found = layout.find_workspace()
    if found is None:
        raise ProviderError(
            "not inside a pykantui workspace",
            hint="Run this from inside one, pass --path, or create one with: kbn init",
        )
    return found


def _confirm_for(args: argparse.Namespace) -> ConfirmPush | None:
    """How this run decides whether to send provider changes.

    ``--dry-run`` always declines, which is what makes it safe: the plan is
    still built, the conflict check still runs, and nothing is sent.
    """
    if args.pull_only or args.dry_run:
        return _decline
    if args.yes:
        return None  # no question asked
    if not sys.stdin.isatty():
        # A pipe is not consent. Automation must say --yes explicitly; without
        # it we still build and print the plan and perform the safe pull.
        return _decline
    return _ask


def _decline(plan: SyncPlan) -> bool:
    print(plan.describe())
    print("\n(nothing sent)")
    return False


def _ask(plan: SyncPlan) -> bool:
    print(plan.describe())
    if plan.conflicts():
        print("\n  Conflicts are skipped unless you pass --force.")
    answer = input("\nsend these? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _report(report: SyncReport) -> None:
    print(report.summary())

    for key, why in report.skipped:
        print(f"  skipped {key}: {why}")
    if report.held:
        # Drafts and edits are both "held", but calling a story nobody has sent
        # yet an "unsent edit" reads as though something went wrong with it.
        drafts = [name for name in report.held if is_draft(Path(name).stem)]
        edits = [name for name in report.held if name not in drafts]
        if drafts:
            print(f"  kept your drafts: {', '.join(drafts)}")
        if edits:
            print(f"  kept your unsent edits in: {', '.join(edits)}")

    if report.archived:
        print(f"  archived (no longer yours): {', '.join(report.archived)}")
    if report.considered and report.mine != report.considered:
        skipped = report.considered - report.mine
        print(f"  {report.mine} of {report.considered} issues are yours ({skipped} not written)")

    cache = report.cache
    if cache is not None:
        print(f"  cache: {cache.summary()}")
