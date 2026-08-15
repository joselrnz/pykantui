"""The workspace as a picture: issues, their columns, and what they belong to.

Reads the markdown a sync produced and emits one self-contained HTML file.
Nothing is fetched, nothing is loaded from a CDN, and no JavaScript library is
involved -- the graph is SVG generated here, so the file opens anywhere and
keeps working offline five years from now.

**Lanes, not a force-directed blob.** A kanban already has a strong horizontal
order -- the columns -- and throwing that away to let nodes settle wherever
physics puts them loses the one thing the reader already understands. Columns
are lanes; parent links are curves drawn across them. An epic with children
scattered over four columns is then immediately visible as exactly that, which
is the question this picture exists to answer.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pykantui.tracker.models import RemoteColumn, RemoteProject
from pykantui.workspace import layout, markdown
from pykantui.workspace.layout import ColumnStyle
from pykantui.workspace.state import SyncState
from pykantui.workspace.status import SyncStatus, status_of

#: Node geometry, in SVG units. Wide enough for a key and a truncated title.
NODE_W = 190
NODE_H = 54
LANE_GAP = 74
ROW_GAP = 26
PAD_X = 34
PAD_Y = 108


@dataclass
class Node:
    key: str
    title: str
    column: str
    lane: int
    status: SyncStatus
    issue_type: str = ""
    assignee: str = ""
    parent: str = ""
    url: str = ""
    row: int = 0

    @property
    def x(self) -> float:
        return PAD_X + self.lane * (NODE_W + LANE_GAP)

    @property
    def y(self) -> float:
        return PAD_Y + self.row * (NODE_H + ROW_GAP)


@dataclass
class Graph:
    project: str
    provider: str
    columns: list[str] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)

    def by_key(self) -> dict[str, Node]:
        return {node.key: node for node in self.nodes}

    def edges(self) -> list[tuple[Node, Node]]:
        """Parent → child, for parents that are actually on this board.

        A child whose epic lives in another project has a ``parent`` we cannot
        draw. Dropping the edge is right -- inventing a node for something not
        in the workspace would be drawing something we have not read.
        """
        index = self.by_key()
        return [(index[node.parent], node) for node in self.nodes if node.parent and node.parent in index]

    def orphans(self) -> list[Node]:
        index = self.by_key()
        return [n for n in self.nodes if not n.parent or n.parent not in index]

    def width(self) -> float:
        return PAD_X * 2 + max(1, len(self.columns)) * (NODE_W + LANE_GAP) - LANE_GAP

    def height(self) -> float:
        rows = max((node.row for node in self.nodes), default=0) + 1
        return PAD_Y + rows * (NODE_H + ROW_GAP) + 40


def read(
    workspace: Path,
    provider: str,
    project: RemoteProject,
    columns: list[RemoteColumn],
    column_style: ColumnStyle = layout.DEFAULT_COLUMN_STYLE,
) -> Graph:
    """Build the graph from the markdown on disk. No network."""
    state = SyncState.load(layout.state_file(workspace))
    folders = layout.folder_index(columns, column_style)
    lane_of = {column.name: index for index, column in enumerate(columns)}

    graph = Graph(
        project=project.key or project.project_id,
        provider=provider,
        columns=[column.name for column in columns],
    )

    used: dict[int, int] = {}
    for path in layout.iter_issue_files(workspace, provider, project):
        try:
            parsed = markdown.read(path)
        except OSError:
            continue
        front = parsed.front
        folder = layout.column_name_of(path, workspace, provider, project)
        column = folders.get(folder)
        name = column.name if column else folder
        lane = lane_of.get(name, 0)

        row = used.get(lane, 0)
        used[lane] = row + 1

        graph.nodes.append(
            Node(
                key=str(front.get("key", "") or path.stem),
                title=str(front.get("title", "") or ""),
                column=name,
                lane=lane,
                row=row,
                status=status_of(path, workspace, provider, project, state, columns, column_style),
                issue_type=str(front.get("type", "") or ""),
                assignee=str(front.get("assignee", "") or ""),
                parent=str(front.get("parent", "") or ""),
                url=str(front.get("url", "") or ""),
            )
        )
    return graph


# ---- rendering -----------------------------------------------------------

_COLOURS = {
    SyncStatus.SYNCED: "var(--node)",
    SyncStatus.EDITED: "var(--amber)",
    SyncStatus.CONFLICT: "var(--red)",
    SyncStatus.NEW: "var(--blue)",
    SyncStatus.INVALID: "var(--red)",
}


def _curve(parent: Node, child: Node) -> str:
    """A cubic from the parent's right edge to the child's left.

    Horizontal control points, so an edge leaves and arrives flat. Crossing
    lanes then reads as a smooth sweep rather than a diagonal that could be
    mistaken for a lane boundary.
    """
    x1, y1 = parent.x + NODE_W, parent.y + NODE_H / 2
    x2, y2 = child.x, child.y + NODE_H / 2
    span = max(40.0, (x2 - x1) * 0.55)
    return f"M {x1} {y1} C {x1 + span} {y1}, {x2 - span} {y2}, {x2} {y2}"


def _node_svg(node: Node) -> str:
    key = html.escape(node.key)
    title = html.escape(node.title[:34] + ("…" if len(node.title) > 34 else ""))
    meta = " · ".join(part for part in (node.issue_type, node.assignee) if part)
    colour = _COLOURS[node.status]
    return f"""<g class="node" data-key="{key}" data-status="{node.status.value}">
  <rect x="{node.x}" y="{node.y}" width="{NODE_W}" height="{NODE_H}" rx="3"/>
  <rect x="{node.x}" y="{node.y}" width="3" height="{NODE_H}" fill="{colour}" class="stripe"/>
  <text class="k" x="{node.x + 14}" y="{node.y + 21}">{key}</text>
  <text class="t" x="{node.x + 14}" y="{node.y + 37}">{title}</text>
  <text class="m" x="{node.x + 14}" y="{node.y + 49}">{html.escape(meta)}</text>
</g>"""


def render(graph: Graph, *, generated: datetime | None = None) -> str:
    """The whole page, as one string. Self-contained by construction."""
    when = (generated or datetime.now()).strftime("%d %b %Y, %H:%M")
    edges = graph.edges()

    lanes = "".join(
        f'<text class="lane" x="{PAD_X + index * (NODE_W + LANE_GAP)}" y="{PAD_Y - 34}">'
        f"{html.escape(name)}</text>"
        f'<line class="lane-rule" x1="{PAD_X + index * (NODE_W + LANE_GAP)}" y1="{PAD_Y - 24}" '
        f'x2="{PAD_X + index * (NODE_W + LANE_GAP) + NODE_W}" y2="{PAD_Y - 24}"/>'
        for index, name in enumerate(graph.columns)
    )
    paths = "".join(
        f'<path class="edge" data-from="{html.escape(p.key)}" data-to="{html.escape(c.key)}" d="{_curve(p, c)}"/>'
        for p, c in edges
    )
    nodes = "".join(_node_svg(node) for node in graph.nodes)

    counts = {status: sum(1 for n in graph.nodes if n.status is status) for status in SyncStatus}
    chips = "".join(
        f'<span class="chip {status.value}"><i></i>{counts[status]} {html.escape(status.label)}</span>'
        for status in SyncStatus
        if counts[status]
    )

    empty = "" if graph.nodes else ('<p class="empty">No issue files found. Run <code>kbn sync</code> first.</p>')

    return _TEMPLATE.format(
        project=html.escape(graph.project),
        provider=html.escape(graph.provider),
        when=html.escape(when),
        issues=len(graph.nodes),
        links=len(edges),
        roots=len(graph.orphans()),
        chips=chips,
        lanes=lanes,
        paths=paths,
        nodes=nodes,
        width=graph.width(),
        height=graph.height(),
        empty=empty,
        data=json.dumps({n.key: n.parent for n in graph.nodes}),
    )


_TEMPLATE = """<style>
:root {{
  --ink:#12151b; --soft:#3d4453; --muted:#69728a; --rule:#e0e4ec;
  --paper:#fcfcfd; --node:#c3cad8; --panel:#fff;
  --blue:#0178d4; --amber:#c77d18; --red:#ba3c5b;
  --mono: ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  --prose: Charter,"Bitstream Charter","Iowan Old Style",Georgia,serif;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --ink:#e8ebf1; --soft:#b6becd; --muted:#8a93a6; --rule:#242932;
    --paper:#0d1015; --node:#39414f; --panel:#141922;
    --blue:#4aa3e8; --amber:#e0a24a; --red:#e0708e; }}
}}
:root[data-theme="dark"] {{ --ink:#e8ebf1; --soft:#b6becd; --muted:#8a93a6; --rule:#242932;
  --paper:#0d1015; --node:#39414f; --panel:#141922;
  --blue:#4aa3e8; --amber:#e0a24a; --red:#e0708e; }}
:root[data-theme="light"] {{ --ink:#12151b; --soft:#3d4453; --muted:#69728a; --rule:#e0e4ec;
  --paper:#fcfcfd; --node:#c3cad8; --panel:#fff;
  --blue:#0178d4; --amber:#c77d18; --red:#ba3c5b; }}

*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--prose);
  font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1240px;margin:0 auto;padding:3rem 1.5rem 4rem;
  display:flex;flex-direction:column;gap:2rem}}
header{{display:flex;flex-direction:column;gap:.55rem;max-width:66ch}}
.eyebrow{{font-family:var(--mono);font-size:.7rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--muted)}}
h1{{margin:0;font-size:clamp(1.6rem,3.4vw,2.1rem);font-weight:600;
  letter-spacing:-.015em;text-wrap:balance}}
.sub{{margin:0;color:var(--soft)}}

.stats{{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}}
.chip{{display:inline-flex;align-items:center;gap:.45rem;font-family:var(--mono);
  font-size:.74rem;padding:.28rem .6rem;border:1px solid var(--rule);
  border-radius:2px;color:var(--soft);background:var(--panel)}}
.chip i{{width:8px;height:8px;border-radius:50%;background:var(--node)}}
.chip.edited i{{background:var(--amber)}}
.chip.conflict i{{background:var(--red)}}
.chip.new i{{background:var(--blue)}}
.count{{font-family:var(--mono);font-size:.74rem;color:var(--muted);
  font-variant-numeric:tabular-nums}}

figure{{margin:0;border:1px solid var(--rule);border-radius:4px;
  background:var(--panel);overflow-x:auto}}
svg{{display:block;min-width:{width}px}}

.lane{{font-family:var(--mono);font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;fill:var(--muted)}}
.lane-rule{{stroke:var(--rule);stroke-width:1}}
.node rect{{fill:var(--panel);stroke:var(--rule);stroke-width:1}}
.node .stripe{{stroke:none}}
.node .k{{font-family:var(--mono);font-size:11px;fill:var(--muted)}}
.node .t{{font-size:13px;fill:var(--ink)}}
.node .m{{font-family:var(--mono);font-size:10px;fill:var(--muted)}}
.edge{{fill:none;stroke:var(--node);stroke-width:1.4}}

.node,.edge{{transition:opacity .12s ease}}
figure.focused .node,figure.focused .edge{{opacity:.22}}
.node.on,.edge.on{{opacity:1}}
.node.on rect:first-child{{stroke:var(--blue)}}
.edge.on{{stroke:var(--blue);stroke-width:2}}
.node{{cursor:pointer}}
@media (prefers-reduced-motion:reduce){{.node,.edge{{transition:none}}}}

.legend{{color:var(--muted);font-size:.86rem;max-width:66ch}}
.empty{{padding:2rem;color:var(--muted);text-align:center}}
code{{font-family:var(--mono);font-size:.85em}}
footer{{border-top:1px solid var(--rule);padding-top:1rem;color:var(--muted);
  font-size:.82rem;max-width:66ch}}
</style>

<div class="wrap">
  <header>
    <span class="eyebrow">{provider} · {project}</span>
    <h1>What belongs to what</h1>
    <p class="sub">Columns run left to right. A curve joins a parent to its
      children, so an epic whose work is scattered across the board shows up as
      exactly that.</p>
  </header>

  <div class="stats">
    <span class="count">{issues} issues</span>
    <span class="count">·</span>
    <span class="count">{links} parent links</span>
    <span class="count">·</span>
    <span class="count">{roots} unparented</span>
    {chips}
  </div>

  <figure id="fig">
    {empty}
    <svg viewBox="0 0 {width} {height}" width="{width}" height="{height}"
         role="img" aria-label="Issue graph for {project}">
      {lanes}
      {paths}
      {nodes}
    </svg>
  </figure>

  <p class="legend">Click a card to isolate it and everything it is joined to.
    Click again, or anywhere else, to bring the rest back.</p>

  <footer>Generated {when} from the markdown in this workspace. No network.</footer>
</div>

<script>
(function () {{
  var fig = document.getElementById("fig");
  if (!fig) return;
  var nodes = Array.prototype.slice.call(fig.querySelectorAll(".node"));
  var edges = Array.prototype.slice.call(fig.querySelectorAll(".edge"));
  var active = null;

  function clear() {{
    active = null;
    fig.classList.remove("focused");
    nodes.concat(edges).forEach(function (el) {{ el.classList.remove("on"); }});
  }}

  function focus(key) {{
    clear();
    active = key;
    fig.classList.add("focused");
    var related = {{}};
    related[key] = true;
    edges.forEach(function (edge) {{
      var from = edge.getAttribute("data-from"), to = edge.getAttribute("data-to");
      if (from === key || to === key) {{
        edge.classList.add("on");
        related[from] = true; related[to] = true;
      }}
    }});
    nodes.forEach(function (node) {{
      if (related[node.getAttribute("data-key")]) node.classList.add("on");
    }});
  }}

  nodes.forEach(function (node) {{
    node.addEventListener("click", function (event) {{
      event.stopPropagation();
      var key = node.getAttribute("data-key");
      if (active === key) clear(); else focus(key);
    }});
  }});
  document.addEventListener("click", clear);
}})();
</script>
"""
