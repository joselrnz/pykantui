"""``kbn graph`` — draw the workspace as one self-contained HTML file.

Reads the markdown a sync produced. Nothing is fetched, so it works offline and
on a workspace someone else committed.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from pykantui.config.paths import write_text_atomic
from pykantui.i18n import translate as _
from pykantui.tracker import ProviderError
from pykantui.workspace import graph as graph_module
from pykantui.workspace import layout
from pykantui.workspace.project import Project

DEFAULT_NAME = "graph.html"


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    summary = _("render the workspace as an HTML graph")
    parser = sub.add_parser("graph", help=summary, description=summary)
    parser.add_argument("--path", type=Path, default=None, help=_("the workspace (default: found by walking up)"))
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=_("where to write it (default: ./{name})").format(name=DEFAULT_NAME),
    )
    parser.add_argument("--open", action="store_true", help=_("open it in a browser afterwards"))


def run(args: argparse.Namespace) -> int:
    try:
        workspace = _find(args.path)
        project = Project.load(workspace)

        # Columns come from the provider, but from its cache -- drawing a board
        # should not need the network any more than opening one does.
        with project.open() as provider:
            columns = provider.columns(project.project_id)
            picture = graph_module.read(workspace, project.provider, project.remote(), columns, project.column_style)
    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    target = (args.out or Path.cwd() / DEFAULT_NAME).expanduser().resolve()
    write_text_atomic(target, graph_module.render(picture))

    print(f"wrote {target}")
    print(f"  {len(picture.nodes)} issues, {len(picture.edges())} parent links")
    if not picture.nodes:
        print("  (nothing to draw — run `kbn sync` first)")

    if args.open:
        webbrowser.open(target.as_uri())
    return 0


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
