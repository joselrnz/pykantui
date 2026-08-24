"""``kbn batch`` — declarative issue generation, review, and apply."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pykantui.batch.executor import apply_batch_plan
from pykantui.batch.models import MAX_BATCH_ISSUES, load_manifest, write_generated_manifest, write_manifest
from pykantui.batch.planner import build_batch_plan, load_batch_plan, write_batch_plan
from pykantui.batch.refinement import apply_refinement, load_refinement
from pykantui.i18n import translate as _
from pykantui.tracker.errors import ProviderError
from pykantui.workspace import layout
from pykantui.workspace.locking import exclusive_workspace
from pykantui.workspace.project import Project


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("generate, plan, and apply declarative issue batches")
    parser = sub.add_parser("batch", help=summary, description=summary)
    actions = parser.add_subparsers(dest="batch_action", required=True)

    jira = actions.add_parser("jira", help=_("generate an explicit Jira issue manifest"))
    jira.add_argument("--count", type=int, default=10, help=_(f"number of issues, 1-{MAX_BATCH_ISSUES}"))
    jira.add_argument("-o", "--output", type=Path, default=Path("issues.yml"))
    jira.add_argument("--force", action="store_true", help=_("replace an existing output file"))

    plan = actions.add_parser("plan", help=_("validate and resolve a manifest without provider writes"))
    plan.add_argument("manifest", type=Path)
    plan.add_argument("-o", "--output", type=Path, default=None)
    plan.add_argument("--path", type=Path, default=None, help=_("workspace (default: found by walking up)"))
    plan.add_argument("--force", action="store_true", help=_("replace an existing plan file"))
    plan.add_argument("--refresh", action="store_true", help=_("refresh provider metadata before planning"))

    apply = actions.add_parser("apply", help=_("apply one exact reviewed batch plan"))
    apply.add_argument("plan", type=Path)
    apply.add_argument("--path", type=Path, default=None, help=_("workspace (default: found by walking up)"))
    apply.add_argument("--yes", action="store_true", help=_("apply without an interactive confirmation"))
    apply.add_argument("--refresh", action="store_true", help=_("refresh provider metadata before applying"))

    refine = actions.add_parser("refine", help=_("apply a reviewed AI proposal to local YAML only"))
    refine.add_argument("manifest", type=Path)
    refine.add_argument("--proposal", type=Path, required=True)
    refine.add_argument("-o", "--output", type=Path, default=None)
    refine.add_argument("--redo-ai", action="store_true", help=_("allow replacing fields previously marked AI"))
    refine.add_argument("--force", action="store_true", help=_("replace an existing output file"))


def run(args: argparse.Namespace) -> int:
    try:
        if args.batch_action == "jira":
            write_generated_manifest(args.output, provider="jira", count=args.count, force=args.force)
            print(f"generated {args.count} Jira issue definitions in {args.output}")
            print("  fill the missing fields, then run: kbn batch plan " + str(args.output))
            return 0
        if args.batch_action == "plan":
            workspace = _find_workspace(args.path)
            project = Project.load(workspace)
            manifest = load_manifest(args.manifest)
            output = args.output or args.manifest.with_suffix(".plan.json")
            with project.open() as provider:
                if args.refresh:
                    provider.refresh()
                plan = build_batch_plan(manifest, args.manifest, provider, project.remote())
            write_batch_plan(output, plan, force=args.force)
            print(plan.describe())
            print(f"\nSaved reviewed plan to {output}")
            print("Apply only this exact plan with: kbn batch apply " + str(output))
            return 0
        if args.batch_action == "apply":
            workspace = _find_workspace(args.path)
            project = Project.load(workspace)
            plan = load_batch_plan(args.plan)
            print(plan.describe())
            if not _confirmed(args.yes):
                print("cancelled; no provider changes were made", file=sys.stderr)
                return 130 if sys.stdin.isatty() else 2
            with exclusive_workspace(workspace), project.open() as provider:
                if args.refresh:
                    provider.refresh()
                report = apply_batch_plan(workspace, provider, project.remote(), plan)
            print("\nApplied batch: " + report.summary())
            return 0
        if args.batch_action == "refine":
            manifest = load_manifest(args.manifest)
            proposal = load_refinement(args.proposal)
            refined = apply_refinement(manifest, proposal, redo_ai=args.redo_ai)
            output = args.output or args.manifest.with_suffix(".refined.yml")
            write_manifest(output, refined, force=args.force)
            print(f"wrote AI-refined local manifest to {output}")
            print("  review the YAML diff, then run: kbn batch plan " + str(output))
            return 0
    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled", file=sys.stderr)
        return 130
    raise AssertionError(f"unknown batch action {args.batch_action!r}")


def _find_workspace(supplied: Path | None) -> Path:
    if supplied is not None:
        return supplied.expanduser().resolve()
    found = layout.find_workspace()
    if found is None:
        raise ProviderError(
            "not inside a pykantui workspace",
            hint="Run this from inside one, pass --path, or create one with: kbn init",
        )
    return found


def _confirmed(assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input("\nCreate these provider issues? [y/N] ").strip().casefold() in {"y", "yes"}
