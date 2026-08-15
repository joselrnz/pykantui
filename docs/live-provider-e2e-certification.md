# Live provider end-to-end certification

This is the execution checklist and evidence contract for the run-tagged live
certification requested on 2026-08-14. It supplements the network-free comment
matrix in `comment-sync-verification-plan.md`; it does not replace CI.

## Safety boundary

- Run ID: `PKT-E2E-20260814T122600Z-3bd16524`. Preflight must prove that exact
  marker is absent on every target before any write; it is never silently
  replaced after the first provider starts.
- Only cards whose title starts with the exact run ID may be mutated.
- Every provider/project identity is read and recorded before writes.
- No pre-existing card, comment, column, label, user, project, or workspace is
  changed or deleted.
- Remote comments are append-only in pykantui and may remain in the sandbox.
- Pykantui does not delete provider cards. Delete journeys therefore remove an
  unsent tagged Markdown draft and prove that no provider delete request occurs.
- Live writes require the explicit `PYKANTUI_LIVE_WRITES=1` gate. Dry-run is the
  default. Secrets and comment bodies are never stored in evidence.
- A failed or uncertain provider write stops that provider lane. It does not
  continue with a guessed remote state.

## Evidence required for every action

Each action produces one manifest row containing run ID, provider, nonsecret
project label/ID, case ID, action, surface (`tui`, `markdown`, `sync`, or
`direct-api`), local card identity, canonical remote identity when assigned,
before/after hashes, expected and observed request counts, result, timestamp,
and paths to the real Textual SVG/PNG captures. Tokens, auth headers, raw API
responses, private bodies, and credential-bearing URLs are forbidden.

The artifact root is `artifacts/live-provider-e2e/<run-id>/`. Its `index.md`
lists every provider and screenshot; `manifest.json` is machine-checkable and
`summary.json` contains counts only.

## Per-provider journey

Run providers sequentially: Asana, ClickUp, GitHub, Jira, Linear, Monday.com,
Plane, Shortcut, and Trello.

1. Verify identity, exact project, columns/states, native issue types, fields,
   comment permissions, and absence of the run marker.
2. Create 20 local Markdown drafts using only fields advertised by that
   configured provider. Include empty/Unicode/long bodies and provider-native
   type, priority, labels, components, due date, and assignee only where valid.
3. Capture Kanban, Rows, Split Info, Split Details, Comments, and Sync preview.
4. Delete two still-local drafts in the TUI and prove zero provider writes.
5. Edit four drafts in the TUI, four by editing Markdown, and stage moves across
   at least three valid workflow columns.
6. Add local comment drafts on run-created cards. Edit one and delete one before
   Sync; prove local save causes zero provider calls.
7. Confirm Sync and capture Preparing, a determinate fraction, completion, and
   any held/error state. Never infer success from the UI alone.
8. Bypass caches and read every run-created card and comment directly through
   its provider client. Compare canonical IDs, title, description/body, state,
   supported fields, and comment hashes against parsed Markdown and TUI state.
9. Repeat Sync and assert zero duplicate creates/comments/edits/moves.
10. Make one safe external change on a run-created card, make a different local
    change, pull, and verify conflict presentation plus both resolution choices.
11. Restart the process/container and prove registry, Markdown, cache, pending
    journal, and TUI state reconstruct the same canonical result.

## Executable edge-case matrix

The runner must emit at least 120 distinct case results. The minimum matrix is
15 common cases across nine providers (135 results):

1. identity/project preflight and marker collision check;
2. 20 draft parse/render round trips;
3. provider-aware optional-field omission;
4. local TUI edit with zero network calls;
5. direct Markdown edit with zero network calls;
6. local draft deletion with zero remote delete calls;
7. valid cross-column move;
8. local comment add/edit/delete before Sync;
9. confirmed create/edit/move/comment request counts;
10. direct uncached API readback equality;
11. second Sync creates no duplicates;
12. concurrent/duplicate F5 remains single-flight;
13. external-versus-local conflict and explicit resolution;
14. restart/cache/journal reconstruction;
15. unsafe input and screenshot/manifest redaction.

Additional shared cases cover zero/one/20/21/100 cards, pagination boundaries,
null/deleted/bot authors, replies, malformed envelopes, 400/401/403/404/409/
422/429/5xx/timeout paths, ambiguous non-idempotent comments, interrupted local
writes, tiny and wide terminals, all themes, CJK/emoji/control input, stale
plans, unsupported fields, missing columns/types, and provider permission loss.

## Markdown and future automation contract

There is one canonical pykantui Markdown envelope. Provider differences are
data-driven field availability, not separate ad-hoc file formats. The generator
must obtain `Provider.editable_card_fields()` and
`Provider.creatable_card_fields()` from the configured instance, plus live
columns/types/components, and include only supported front-matter fields.
Unknown provider fields fail before network access. Remote comments remain in
the provider-owned region, local comment drafts in the pending region, source
description in the source region, and private notes last.

CSV/Excel/LLM import should target a neutral validated draft model first, then
use this same provider capability negotiation and Markdown renderer. It must
not generate provider payloads directly or guess Jira/ClickUp field names.

## Completion gate

Certification is complete only when every configured provider has either a
fully passing live lane or an explicit `BLOCKED` receipt naming the exact
permission/sandbox deficiency; all screenshots are visually inspected; all
manifest files pass schema, uniqueness, hash, redaction, and file-existence
checks; the 120+ case matrix, focused tests, Ruff, mypy, secret scan, and Docker
Linux verification pass; and direct API truth equals Markdown and TUI state.

## Certified live result · 2026-08-14

Run `PKT-E2E-20260814T122600Z-3bd16524` completed the full live lane for all
nine configured providers. The final machine-checked totals are:

- 171 tagged remote cards created and 171 exact, cache-bypassed API reads;
- 18 card updates, nine workflow moves, and nine provider comments;
- nine deliberately-created local/provider title conflicts resolved to the
  provider value, with matching local and remote title hashes;
- nine final no-op syncs with zero provider mutations, 19 remote cards and 19
  byte-stable Markdown files per provider;
- 221 genuine Textual screenshot states, each stored as a paired SVG and PNG
  with SHA-256 and geometry validation (442 visual artifacts total);
- 29 receipt-guarded live operations: 27 verified and two expected failed
  Monday diagnostic operations. The repaired Monday create used a distinct
  operation ID and was never a blind replay.

The evidence bundle is
`artifacts/live-provider-e2e/PKT-E2E-20260814T122600Z-3bd16524/`:

- `index.md` is the complete screenshot gallery;
- `manifest.json` contains every artifact hash, geometry result, provider
  result, and nonsecret receipt terminal state;
- `summary.json` contains counts only;
- each provider has separate create, mutation, conflict, and final no-op direct
  API readbacks.

The live run exposed and fixed nullable Linear team descriptions, Monday's
dynamic status-axis mapping, Shortcut next-page URL normalization, Plane
create-response/draft reconciliation, comment-draft preservation across create,
and literal Trello provider text rendering. Plane's 19 duplicate retained local
drafts were hash-pinned and moved—not deleted—to a run-owned quarantine after
all 19 canonical cards were proven. No pre-existing remote item was modified or
deleted.

### Open the certified workspaces in Docker

From PowerShell at the repository root, Jira can be opened with:

```powershell
cd <repository-root>
docker compose run --rm `
  -e PYKANTUI_HOME=/work/live-e2e/PKT-E2E-20260814T122600Z-3bd16524/jira/.pykantui `
  --workdir /work/live-e2e/PKT-E2E-20260814T122600Z-3bd16524/jira `
  shell kbn --theme cyberpunk
```

Do not add `-T`; the TUI needs the interactive terminal. Replace both `jira`
segments with `asana`, `clickup`, `github`, `linear`, `monday`, `plane`,
`shortcut`, or `trello` to inspect another certified workspace. These
workspaces already contain the live tagged cards; do not rerun the create tools.

Regenerate and revalidate the live manifest without making network calls:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m tools.build_live_evidence_manifest `
  artifacts/live-provider-e2e/PKT-E2E-20260814T122600Z-3bd16524
```
