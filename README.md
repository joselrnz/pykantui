<!-- markdownlint-disable-next-line MD041 -->
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/header-no-frame.png" alt="pykantui — a terminal kanban board with a pluggable task backend" width="100%" style="display:block;" />
</p>

# pykantui

A local-first terminal kanban board with one provider-neutral workflow for
Jira, Linear, Asana, ClickUp, Monday.com, Plane, Trello, GitHub, and
Shortcut. Remote work items become editable Markdown; `kbn sync` reconciles
local edits with the selected provider.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License MIT](https://img.shields.io/badge/license-MIT-0070F3)
![Built with Textual](https://img.shields.io/badge/built%20with-Textual-7928CA)

## Install

Requires Python 3.11 or newer.

```bash
pip install pykantui         # or: pipx install pykantui
kbn demo
```

To work on pykantui itself, install it editable from a clone:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\kbn.exe demo
```

```bash
python -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/kbn demo
```

## Quick start

**1. Look around first.** `demo` opens a throwaway board with sample cards
and touches nothing on disk — drive it with the keys from
[Driving the board](#driving-the-board), then `ctrl+q` to leave:

```bash
kbn demo
```

**2. Connect your tracker.** `init` is one guided session: pick your provider
(Jira, Linear, Asana, ClickUp, Monday, Plane, Trello, GitHub, or Shortcut),
paste its API token, choose a project and a folder. It writes one Markdown
file per work item and stores the token outside the workspace:

```bash
kbn init
```

**3. Work.** Open the board, edit cards in the TUI or the Markdown files in
any editor, and sync when you are ready — every outbound change is shown for
review and sent only after you confirm:

```bash
kbn           # open the board for the workspace you are in
kbn sync      # review what would change, confirm, push, then pull
```

That is the whole loop. The rest of this page is reference:

| Command | What it opens |
| --- | --- |
| `kbn demo` | a throwaway board with sample tasks |
| `kbn init` | create a Markdown workspace from any supported provider |
| `kbn` | the board for the current workspace |
| `kbn sync` | reconcile workspace Markdown and its provider |
| `kbn show` | the board as plain text, no TUI |
| `kbn board` | your local, tracker-free board |

| Flag | Effect |
| --- | --- |
| `--movement adjacent\|jump` | how `H`/`L` behave |
| `--no-confirm` | apply column moves without the confirmation dialog |
| `--theme NAME` | any Textual theme; saved to `config.json` |
| `--edges round\|square` | corner style for every border at once; saved |
| `--locale auto\|en\|es\|…` | interface language, one of twenty; saved to `config.json` |

Token setup per provider is documented in `.env.example`, and
[Provider workspaces](#provider-workspaces) covers the workspace lifecycle in
depth.

## Demos (interactive)

### View tags (rows component)

<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/demo-view-tags.gif" alt="Rows view action flow with rows, push/move/edit, and dialogs" width="100%" />
</p>
<ul>
  <li>Row actions: <strong>n</strong> / <strong>e</strong> / <strong>d</strong> for local edits</li>
  <li>Push path: <strong>F5</strong> → confirm → sync status/progress dialog</li>
  <li>Move path: <strong>H</strong>/<strong>L</strong> then confirm dialog (or jump mode flow)</li>
  <li>Menu path: open/close Rows component menus and run actions inline</li>
  <li>Approval/status indicators: held items, conflicts, and sync status are visible</li>
</ul>

### Kanban view

<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/demo-kanban.gif" alt="Kanban view action flow with drag, menu, and dialogs" width="100%" />
</p>
<ul>
  <li>Card movement is routed through the same confirmation and sync flow</li>
  <li>Push/menu/close cycles keep state in the same progress and notification model</li>
  <li>Use this view to validate move and approval behavior before provider writes</li>
</ul>

### Sidebar view

<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/demo-sidebar.gif" alt="Sidebar panel movement with inline details and panel controls" width="100%" />
</p>
<ul>
  <li>Split behavior: <strong>[</strong> / <strong>]</strong> and <strong>\\</strong> panel control</li>
  <li>Open/close menu while moving or editing cards</li>
  <li>Status and approval: explicit signals for queued/held/ready-to-apply work</li>
</ul>

## Demo gallery by provider

### Asana
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-asana.gif" alt="pykantui Asana local action flow" width="100%" />
</p>

### ClickUp
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-clickup.gif" alt="pykantui ClickUp local action flow" width="100%" />
</p>

### GitHub
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-github.gif" alt="pykantui GitHub local action flow" width="100%" />
</p>

### Jira
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-jira.gif" alt="pykantui Jira local action flow" width="100%" />
</p>

### Linear
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-linear.gif" alt="pykantui Linear local action flow" width="100%" />
</p>

### Monday
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-monday.gif" alt="pykantui Monday local action flow" width="100%" />
</p>

### Plane
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-plane.gif" alt="pykantui Plane local action flow" width="100%" />
</p>

### Shortcut
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-shortcut.gif" alt="pykantui Shortcut local action flow" width="100%" />
</p>

### Trello
<p>
  <img src="https://raw.githubusercontent.com/joselrnz/pykantui/main/assets/live-real-9x1-trello.gif" alt="pykantui Trello local action flow" width="100%" />
</p>

## Contents

- [Install](#install) and [Quick start](#quick-start)
- [Driving the board](#driving-the-board) — keys, movement, confirmation, collapsing
- [The top bar](#the-top-bar) — search, filtering, sorting
- [Shaping the board](#shaping-the-board) — columns as configuration
- [Cards from the command line](#cards-from-the-command-line)
- [Backends](#backends) and [Provider workspaces](#provider-workspaces)
- [Where things are stored](#where-things-are-stored)
- [Languages](#languages)
- [How it is built](#how-it-is-built) — layout, actions, the write path
- [Develop](#develop) and [Recording the demo](#recording-the-demo)

## Driving the board

Navigation and movement are deliberately different keys: **lowercase moves the
cursor, uppercase moves the card.**

| Key | Effect |
| --- | --- |
| `h` `j` `k` `l` / arrows | move focus (wraps; empty columns are skipped) |
| `H` / `L` | move the focused card left / right across columns |
| `J` / `K` | reorder the card within its column |
| `enter` | commit a pending move (jump mode) |
| `n` / `e` / `d` | new / edit / delete |
| `i` | flash the cards blocking this one |
| `v` / double-click | open the card: dates, dependencies, description, provider fields |
| `,` / right-click / click `▾` | the column dropdown |
| `z` / `Z` | collapse the focused column / expand every column |
| `m` | toggle adjacent ↔ jump movement mode |
| `c` | toggle the move confirmation |
| `r` | reload from the backend |
| `/` | jump to search |
| `F2` | cycle the top bar |
| `ctrl+q` | quit |

Mouse drag works too, with the drop position taken from card midpoints.

### Movement modes

- **adjacent** — `H`/`L` commits to the neighbouring column immediately.
- **jump** — `H`/`L` highlights a candidate column and waits 1.2 s for `enter`.
  Pressing `H`/`L` again walks the highlight further, so crossing three columns
  is one backend write instead of three. That matters against Jira, where each
  write is an HTTP round-trip.

### Move confirmation

Changing a card's column asks first. `enter`/`y` approves, `escape`/`n`
cancels. Nothing is written until you approve, so cancelling leaves both the
board and the store untouched.

```text
                 Move this card?

             Wire up the Jira backend
                 Ready  →  Doing

              [ Move ]    [ Cancel ]
```

The dialog names the side effect when there is one: moving into the finish
column marks the task finished, into the reset column clears the dates, and on
a read-only backend the move writes to Jira.

It applies to column moves only. `J`/`K` reordering does not ask, and neither
does a move the dependency gate is going to refuse — you get the "blocked"
toast instead of a pointless question.

Turn it off with `c` at runtime or `--no-confirm` at launch.

### Collapsing columns

`z` shrinks the focused card's column to a 5-cell strip showing the count and
the name read downward; the columns left open share the freed width. Click the
`«` in a header to collapse, click the strip to reopen, or press `Z` to expand
everything.

```text
╭──────────────────────────╮╭──────────────────────────╮╭───╮
│           Ready        « ││           Doing        « ││ » │
│ Wire up the Jira backend ││ Read the reference clone ││   │
│ Add a settings screen    ││                          ││ 1 │
│ Ship 0.1.0               ││                          ││   │
│                          ││                          ││ D │
│                          ││                          ││ O │
│                          ││                          ││ N │
│                          ││                          ││ E │
╰──────────────────────────╯╰──────────────────────────╯╰───╯
```

A collapsed column is hidden, not closed:

- **It stays a move target.** `L` into a collapsed Done still files the card and
  the strip's count goes up. Focus stays on a visible card rather than following
  the card somewhere you cannot see.
- **Navigation skips it.** `h`/`l` step over collapsed columns.
- **Its cards are untouched.** Nothing is archived or dropped.
- **The last open column will not collapse** — you would be left with no board.

The JSON backend persists the state, so a board you left with Done collapsed
opens that way. Jira keeps it for the session only.

## The top bar

One bar, three levels. `F2` cycles them, or click the caret at the right.

```text
 ≡                                                       20 cards  ▾    collapsed
 ≡  search…   Filter  Sort  Columns  View  Help          20 cards  ▾    toolbar
 ≡  search…   Filter  Sort  Columns  View  Help          20 cards  ▴    expanded
 ┌ Provider scope ─┐ ┌ Workflow state ─┐ ┌ Assignee/owner ─┐ ┌ Type, when supported ─┐
 ┌ Provider ID ─┐ ┌ From ──┐ ┌ Until ─┐ ┌ Sort ─┐  Jira only: [ ] Sprint ┌ JQL ─┐ [Search]
 ┌ State ────┐ ┌ Saved ────┐  ⇵ Reverse   + Save   Clear   New card   Refresh
```

The level you leave it at is saved and comes back next time. **The count shows
at every level**, reading `9 of 27 · overdue` when something is filtering — a
filter you forgot about is never invisible, even with the bar collapsed.

Provider boxes come from that provider's typed contract. Trello says
Board/List/Member/Card ID; Shortcut says Workflow/Workflow State/Owner/Story
Type; Jira alone adds Sprint and JQL. Unsupported boxes are absent. Jira's live
query controls remain visible but disabled in an offline Markdown workspace.

Every dropdown has a shortcut that opens the panel and jumps straight to it:

| Key | Field | Key | Field | Key | Field |
| --- | --- | --- | --- | --- | --- |
| `p` | Provider scope | `w` | Provider ID | `o` | Sort |
| `t` | Type, when supported | `f` | From | `g` | Saved |
| `s` | Workflow state | `u` | Until | `x` | Sprint, Jira only |
| `a` | Assignee/owner | `y` | Local state | `q` | JQL, Jira only |

### Filtering

| Group | What it matches |
| --- | --- |
| Search | title and description, ignoring case |
| State | blocked · unblocked · overdue · due today · no due date · has notes |
| Provider | only fields declared by that tracker: assignee/owner, type, priority, labels/tags |
| Saved | your named combinations, stored in `config.json` |

Conditions are **cumulative**: Overdue plus Has notes means both, not either.
`Clear` resets the filter and the sort together.

Blocked is computed once per refresh from the whole task list rather than asked
per card, so filtering a Jira board is one request, not one per card.

### Sorting

Manual · Title · Due · Age · Priority, with a Reverse toggle. Sorting is a
**view**: it never writes positions, so the order you arranged by hand survives
underneath and comes back exactly when you pick Manual again.

The trade is that `J`/`K` reordering is disabled while a sort is on — there is
nowhere for it to write. The binding greys out rather than failing on press.

## Shaping the board

Columns are configuration, not code. They live in one file that both backends
read, so a card means the same thing locally and in Jira:

```powershell
kbn columns                      # what the board looks like now
kbn columns add Blocked --after "In Progress" --statuses "BLOCKED, ON ICE"
kbn columns count 8              # grow or shrink to 8 visible columns
kbn columns move Done 1          # reorder
kbn columns rename Waiting "On Hold"
kbn columns role finish Shipped  # which column means finished
kbn columns remove Waiting       # its cards move left; use --move-to to choose
kbn columns hide Archive         # keep it as a target without showing it
kbn columns reset --yes          # back to the defaults below
```

| Command | What it does |
| --- | --- |
| `list` | columns in order, with roles, hidden flags and Jira statuses |
| `add NAME` | `--after` to place it, `--statuses` to map Jira, `--hidden` to start hidden |
| `rename COL NAME` | rename in place |
| `remove COL` | delete it; cards move to `--move-to`, or the first column |
| `move COL N` | put it at 1-based position N |
| `count N` | grow with `Column N` placeholders, or shrink from the right |
| `role reset\|start\|finish [COL]` | set which column stamps dates; omit COL to clear |
| `statuses COL "A, B"` | Jira statuses landing here; `""` clears |
| `show COL` / `hide COL` | visibility without deleting |
| `reset --yes` | restore the defaults |

Columns are addressed by **id** (`#3`), **name** (`"Needs Review"`) or **1-based
position** — whichever is handier. Nothing assumes a column count: one column
works, twelve works.

**Boards already open pick changes up on `r`.** Run `kbn columns add ...` in one
terminal, press `r` in another, and the board rebuilds with the new shape. No
restart, however many are open.

Roles are stored as column **ids**, not positions, so reordering never silently
changes which column means "done". Deleting a role column clears the role rather
than leaving it dangling. `config.json` is meant to be edited by hand, so a
value that is not one of ours falls back to the default instead of raising — a
typo in the file is never the reason the board will not open.

### Widths

Columns share the available width while they fit and stop shrinking at 20 cells,
after which the board scrolls sideways. That floor is why a ten- or twelve-column
board stays usable in an 80-column terminal. Collapsing a column hands its width
back to the rest.

### Defaults

The starting shape, written out on first run from
[`core/workflows.py`](https://github.com/joselrnz/pykantui/blob/main/src/pykantui/core/workflows.py):

| # | Column | Jira statuses | Effect on landing |
| --- | --- | --- | --- |
| 1 | To Do | `BACKLOG`, `TO DO` | clears the start and finish dates |
| 2 | In Progress | `IN PROGRESS` | stamps the start date |
| 3 | Needs Review | `NEEDS REVIEW` | nothing |
| 4 | Waiting | `NEEDS MORE INFO`, `WAITING ON HOLD`, `WAITING OR ON HOLD` | nothing |
| 5 | Done | `DONE`, `CANCEL` | stamps the finish date |
| 6 | Archive | — | hidden by default |

**Needs Review is a stage of the work**, so it sits in the flow between In
Progress and Done. **Waiting is a parked state** — blocked on someone else, or
missing information — which is why it is not on the straight line to Done.

Neither stamps a date. Work under review or on hold is not un-started and not
finished, so a card in either keeps the start date it already had and picks
straight back up when it moves on.

The dependency gate only guards In Progress and Done. A blocked card can sit in
Needs Review or Waiting; it cannot be claimed as started or done.

## Cards from the command line

```powershell
kbn task add "Write the docs"                    # one card in the first column
kbn task add Task --count 30 --column "To Do"    # Task 01 .. Task 30
kbn task add "Ship it" --column Done --description "the details"
kbn task rm 4 7 9                                # delete by id
kbn task clear "To Do" --yes                     # empty a column
```

Counts are zero-padded to the width of the total, so 30 cards come out
`Task 01 … Task 30` and sort in the order you meant. Columns are addressed the
same way as in `kbn columns`. Editing a card is still the TUI's job (`e`).

## Backends

[`Backend`](https://github.com/joselrnz/pykantui/blob/main/src/pykantui/sync/base.py) is the whole contract: four abstract
methods plus optional writes. Anything store-specific rides in `Task.metadata`
rather than becoming a domain field.

| Backend | Writable | Reorder | Query | Notes |
| --- | --- | --- | --- | --- |
| `json` | yes | yes | no | default; one readable file per board |
| provider workspace | yes | provider-specific | provider-specific | local Markdown plus registry provider |

Every capability only some stores have is a method with a default on the base
class, not an attribute the UI goes looking for. The app asks the backend
questions — `writable`, `supports_reorder`, `supports_query`, `query_text()`,
`sprint_only()` — and never rummages through its attributes to guess.

## Provider workspaces

All remote services use the same commands and lifecycle:

```powershell
kbn init                         # choose a provider, project, and folder
kbn init --type jira             # start directly with Jira
kbn init --list-types            # list every registered provider
kbn sync                         # compare, confirm, push local edits, then pull
kbn                              # open the current workspace board
```

`kbn init` keeps its logo, spinner, provider picker, folder picker, progress,
and final board inside one TUI session. Provider field definitions supply the
wizard labels and validation, so adding a provider does not add another CLI
command.

Each workspace contains `.pykantui/project.json` and one Markdown file per work
item. Credentials live in the global `auth.json`, outside the workspace.
`api/` owns the shared HTTP transport, bounded read retries, pagination, error
translation, and response caching. Each `providers/<name>/client.py` owns that
service's authentication; its provider package owns endpoints and field
mapping. See [Provider architecture](https://github.com/joselrnz/pykantui/blob/main/docs/provider-architecture.md).

Jira still preserves its real behavior inside `providers/jira`: Jira Cloud
basic authentication, JQL search, board column discovery, issue creation,
editable fields, comments, and workflow transitions. It no longer requires a
separate SDK or a Jira-only `kbn jira` path.

## Where things are stored

| File | What |
| --- | --- |
| `%LOCALAPPDATA%\pykantui\board.json` | the local board's cards |
| `%LOCALAPPDATA%\pykantui\config.json` | columns, roles, saved filters, theme, locale |
| `%LOCALAPPDATA%\pykantui\auth.json` | credentials for all providers |
| `<workspace>\.pykantui\project.json` | provider/project metadata; no secrets |
| `<workspace>\.pykantui\cache\` | ignored provider response cache |
| `<workspace>\.git\` | local-only history checkpoints; never pushed by pykantui |

On Linux and macOS the base is `$XDG_DATA_HOME` or `~/.local/share`.
`PYKANTUI_HOME` overrides the lot, which is how the tests and the demo board
never touch anything real. Every write goes to a temp file and is renamed over
the target, so an interrupted save leaves the old file intact rather than half
of a new one.

## How it is built

```text
src/pykantui/
  cli/        argument parsing and dispatch, nothing else
  commands/   one module per provider-neutral kbn subcommand
  api/        shared HTTP transport, retry policy, cache, pagination and errors
  config/     where data lives (paths.py) and the saved board shape (board.py)
  core/       board logic no screen or store owns: actions, filters, workflows
  models/     the domain objects and the enums they are built from
  pages/      full-screen views pushed over the board: detail, edit, menu, confirm
  providers/  one package per service: field contract and endpoint mapping
  sync/       local JSON and provider-workspace Backend adapters
  tracker/    provider protocol, registry and provider-neutral remote models
  tui/        the app, its themes, and the widgets on the board
tools/        dev scripts: gif and screenshot rendering
```

Dependencies run one way. `models` depends on nothing, `core` on models,
`api` knows HTTP but no provider, `tracker` owns neutral remote types, providers
translate their APIs into those types, `sync` adapts them to board tasks, and
`tui`/`pages` only consume backend capabilities.

### What a click means

Every clickable thing has to say what it stands for, and the only channel a
widget id or an option id gives you is a string. So there is a wire format,
`"kind:value"` — parsed into an `Action` at the boundary and never picked apart
again:

```python
Action.parse("sort:due")                       # Action(kind=ActionKind.SORT, value="due")
Action.of(ActionKind.ACT, Act.CLEAR).chip_id   # "chip-act-clear"
Action.from_chip_id("chip-act-clear")          # back again
```

[`core/actions.py`](https://github.com/joselrnz/pykantui/blob/main/src/pykantui/core/actions.py) holds the vocabulary —
`ActionKind`, `Menu`, `Act`, `ViewToggle`, `ColumnCommand`, `HelpTopic` — as
enums, so a misspelled action is a parse that returns `None` at one known place
rather than a branch that silently never fires. The app dispatches with a single
`match` over `ActionKind`, which the type checker can see through.

The board re-renders only when the view actually changed. Re-syncing a dropdown
to the value it already holds posts a Changed event, and rebuilding for that
drops the focused card — which at startup means the first key press after
opening goes nowhere.

### How a move works

Keyboard and mouse both converge on `KanbanBoard.request_move`, and every column
move goes through it:

1. check dependencies (`Task.can_move_to`) — refuse with a toast if blocked,
2. ask for confirmation, and stop here if cancelled,
3. hand off to `commit_move`, the only function that writes a move,
4. **write to the backend**,
5. bail out with a toast if the write failed — the board is untouched, so there
   is nothing to roll back,
6. only then move the widget and restack the columns.

`request_move` is a Textual worker rather than a plain coroutine, because
awaiting a modal needs one. That matters in tests: a bare `pilot.pause()` can
return before the move has landed, so `tests/integration/tui/test_board_tui.py` has a `settle`
helper that also drains workers.

## Languages

The application shell ships twenty interface languages through Python's
standard `gettext` catalogs: Arabic, Dutch, English, French, German, Hindi,
Indonesian, Italian, Japanese, Korean, Polish, Portuguese (Brazilian),
Russian, Simplified and Traditional Chinese, Spanish, Thai, Turkish,
Ukrainian, and Vietnamese. Provider names, card content, remote comments, and
local Markdown are user data and are never translated.

```powershell
kbn --locale es                  # use and save Spanish
$env:PYKANTUI_LOCALE = "ja"      # choose Japanese for this environment
kbn --locale auto                # follow the terminal or operating system
```

The precedence is an explicit `--locale`, `PYKANTUI_LOCALE`, the saved locale,
the standard POSIX locale variables, the operating-system locale, then English.
Regional forms resolve to their language — `es-MX` and `es_ES.UTF-8` select
Spanish, `zh_CN` Simplified and `zh_TW`/`zh_HK` Traditional Chinese — while a
regional form whose vocabulary the catalog does not carry (`pt_PT` against the
Brazilian catalog) falls back to English rather than reading wrong.

When adding or changing application-owned text, update and compile the catalog:

```powershell
python -m babel.messages.frontend extract -F babel.cfg -o src/pykantui/i18n/locales/pykantui.pot .
python -m babel.messages.frontend update -i src/pykantui/i18n/locales/pykantui.pot -d src/pykantui/i18n/locales -D pykantui
python -m babel.messages.frontend compile -d src/pykantui/i18n/locales -D pykantui
```

`Babel` is a development dependency; installed users only need the compiled
`.mo` catalog bundled in the wheel.

## Develop

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .

# Authoritative Linux gates, including an installed-wheel smoke test
docker compose run --rm checks
docker compose run --rm tests
docker compose run --rm smoke
docker compose run --rm coverage
```

Baseline: **ruff clean, mypy strict clean (376 files), 1,845 tests OK.**
The randomized coverage gate measures branches as well as statements and
enforces the current 80% baseline. Raise the floor as mocked coverage expands
across provider-specific remote paths.

| Exhaustive shard | Tests |
| --- | --- |
| `test_[a-f]*.py` | 424 |
| `test_[g-m]*.py` | 541 |
| `test_[n-s]*.py` | 422 |
| `test_[t-z]*.py` | 458 |

Pass `-t .` so `tests` is imported as a package. Its `__init__.py` does two
things that only work if it is: it points `PYKANTUI_HOME` at a throwaway
directory for the whole run, so a test that forgets to sandbox itself cannot
rewrite your real board, and it quietens the asyncio logger that
`IsolatedAsyncioTestCase` turns to debug — booting a Textual app trips its
slow-callback warning constantly and buries the results.

The TUI suites are slow because each interaction test boots a real Textual app
under the pilot. Run them by module while iterating, or run the four filename
shards concurrently for a bounded exhaustive gate. Check the test *count*, not
just the verdict: a module that fails to import is reported as one error and
the suite still ends with a summary line.

The suite is organized by responsibility: `tests/unit/` holds isolated API,
provider, tracker, workspace, Git, and TUI contracts; `tests/integration/`
contains CLI, sync, and real Textual-pilot journeys; `tests/edge_cases/` owns
Markdown, path, conflict, output-safety, and property boundaries. Live account
audits are isolated under `tests/live/` and run only through the `live` service.

## Recording the demo

The gif at the top is generated, not captured by hand:

```powershell
.\.venv\Scripts\python.exe -m pip install pillow
.\.venv\Scripts\python.exe tools\gif.py          # assets/demo.gif

# Capture local provider screenshots (no writes)
.\.venv\Scripts\python.exe tools\live_workspace_screenshots.py \
  --workspace-root .docker-workspace/live-e2e/PKT-E2E-20260814T122600Z-3bd16524 \
  --output artifacts/live-provider-assets-PKT-ASSETS-20260814T202943 \
  --run-tag PKT-ASSETS-20260814T202943 \
  --stage provider-snapshots

# Drive a local add/edit/comment flow + screenshots on the same seeded run
.\.venv\Scripts\python.exe tools\live_tui_actions.py \
  --workspace-root .docker-workspace/live-e2e/PKT-E2E-20260814T122600Z-3bd16524 \
  --artifacts artifacts/live-provider-assets-PKT-E2E-20260814T122600Z-3bd16524 \
  --run-tag PKT-E2E-20260814T122600Z-3bd16524 \
  --provider asana --provider clickup --provider github --provider jira --provider linear --provider monday --provider shortcut --provider trello
```
Plane is currently excluded because this environment returns `403` when Plane
state refresh is attempted for the configured workspace.

[`tools/gif.py`](https://github.com/joselrnz/pykantui/blob/main/tools/gif.py) runs the app under Textual's pilot against a
throwaway in-memory board, reads each screen straight off the compositor as
styled cells, draws it with Pillow, and stitches the frames with ffmpeg. No
terminal recorder, no browser and no pty, which is why it runs the same on a
laptop and in CI. Edit the `SCRIPT` list at the top of the file to change what
the demo does.

[`tools/screenshots.py`](https://github.com/joselrnz/pykantui/blob/main/tools/screenshots.py) does the same for stills, writing
SVGs — text, so they render crisply at any size and a diff shows what actually
changed instead of a wall of binary.

[`tools/demo.tape`](https://github.com/joselrnz/pykantui/blob/main/tools/demo.tape) is a [vhs](https://github.com/charmbracelet/vhs)
script for the same demo, kept for anyone who has vhs working: it needs ttyd and
a headless Chromium, which is exactly the machinery `tools/gif.py` avoids.

## Live verification: one real card per provider

We keep a reproducible proof bundle for real-provider creation/sync validation:

- Run tag: `PKT-LIVE-REAL-20260814T133200Z`
- Workspace root (inside the repo, gitignored):
  `.docker-workspace\live-e2e\PKT-E2E-20260814T122600Z-3bd16524`
- Validation report:
  `...\\live-create-artifacts\\validation-PKT-LIVE-REAL-20260814T133200Z.json`
- Screenshots (PNG+SVG): one image per provider, per layout (kanban/rows/split):
  `...\\live-create-artifacts\\PKT-LIVE-REAL-20260814T133200Z\\live-local\\<provider>\\live-real-9x1-*.{png,svg}`
- GIFs:
  `assets/live-real-9x1-asana.gif`
  `assets/live-real-9x1-clickup.gif`
  `assets/live-real-9x1-github.gif`
  `assets/live-real-9x1-jira.gif`
  `assets/live-real-9x1-linear.gif`
  `assets/live-real-9x1-monday.gif`
  `assets/live-real-9x1-plane.gif`
  `assets/live-real-9x1-shortcut.gif`
  `assets/live-real-9x1-trello.gif`

```powershell
# Rebuild exactly this bundle in place (no provider writes in this script)
.\.venv\Scripts\python.exe tools\live_workspace_screenshots.py \
  --workspace-root .docker-workspace/live-e2e/PKT-E2E-20260814T122600Z-3bd16524 \
  --output .docker-workspace/live-e2e/PKT-E2E-20260814T122600Z-3bd16524/live-create-artifacts/PKT-LIVE-REAL-20260814T133200Z/live-local \
  --run-tag PKT-LIVE-REAL-20260814T133200Z \
  --stage live-real-9x1

# Recreate the per-provider GIFs from the provider PNG captures.
# (This script uses Pillow and does not require the shell-level filtergraph.
# It includes kanban/rows/split frames per provider.)
.\.venv\Scripts\python.exe -c "from pathlib import Path; from PIL import Image; base=Path('.docker-workspace/live-e2e/PKT-E2E-20260814T122600Z-3bd16524/live-create-artifacts/PKT-LIVE-REAL-20260814T133200Z/live-local'); out=Path('assets'); providers=['asana','clickup','github','jira','linear','monday','plane','shortcut','trello'];\nfor p in providers:\n    d=base/p\n    frames=[Image.open(d/'live-real-9x1-kanban.png').convert('RGB'), Image.open(d/'live-real-9x1-rows.png').convert('RGB'), Image.open(d/'live-real-9x1-split.png').convert('RGB')]\n    frames[0].save(out/f'live-real-9x1-{p}.gif', save_all=True, append_images=frames[1:], duration=1400, loop=0)"
# Combined GIF across all provider captures (one frame per provider):
.\.venv\Scripts\python.exe -c "from pathlib import Path; from PIL import Image; import re\nbase=Path('.docker-workspace/live-e2e/PKT-E2E-20260814T122600Z-3bd16524/live-create-artifacts/PKT-LIVE-REAL-20260814T133200Z/live-local'); out=Path('assets')/ 'live-real-9x1.gif'; files=sorted(list(base.glob('*\\live-real-9x1-*.png')), key=lambda p: p.as_posix());\nframes=[]\nfor p in files:\n    if re.search(r'/[a-z]+/live-real-9x1-(kanban|rows|split)\\.png$', p.as_posix()):\n        frames.append(Image.open(p).convert('RGB'))\nframes[0].save(out, save_all=True, append_images=frames[1:], duration=800, loop=0)"
```

This run is used as the current all-provider real-card evidence bundle (create/edit
path + readback + TUI capture).

## License

MIT.
