# Comment sync verification capability

## Capability

A user can read provider discussion threads, write or edit an unsent local
comment draft, review the exact outbound operation, explicitly sync it, and
then see the provider's canonical result reflected identically in Markdown and
the TUI. This guarantee applies to Asana, ClickUp, GitHub, Jira, Linear,
Monday.com, Plane, Shortcut, and Trello without hiding provider-specific API
semantics behind a false common denominator.

This document is both the implementation contract and the test program. A
provider is not considered verified merely because its mapper unit test passes:
the complete provider payload -> neutral model -> Markdown -> backend -> TUI ->
local draft -> sync plan -> provider payload -> canonical read-back cycle must
agree.

## Source-of-truth hierarchy

When evidence disagrees, use this order:

1. The provider's current official API documentation, OpenAPI document, or
   published GraphQL schema defines the wire contract.
2. A captured, redacted sandbox response confirms runtime behavior for the
   exact API version and credential type.
3. Provider adapter schemas, mappers, payload builders, and MockTransport tests
   encode that contract locally.
4. `RemoteComment` and `CommentDraft` define the provider-neutral boundary.
5. Markdown is the durable local representation.
6. Backend and TUI state are projections of Markdown, never a competing source
   of truth.

A live response may reveal undocumented behavior, but it must not silently
weaken validation. Record the discrepancy and fail closed until the accepted
shape is deliberate and tested.

## Fixed constraints and invariants

- Remote comments are provider-owned snapshots. Pykantui currently reads them
  but does not edit or delete them.
- Only a `CommentDraft` may become an outbound create operation. Editing an
  unsent draft changes the single future POST; deleting its whole record
  cancels it.
- Saving in the TUI or editing Markdown never contacts a provider. Only an
  explicitly confirmed Sync may write remotely.
- Pull-only sync, declined confirmation, invalid Markdown, insufficient
  permission, and definite provider rejection preserve every local draft.
- A comment POST is non-idempotent unless a provider explicitly supplies an
  idempotency mechanism. It is never blindly retried.
- Before POST, a content-free durable journal records the local draft identity
  and signature. A possibly accepted write remains held until an operator
  verifies provider truth and explicitly retries.
- The local draft is removed only after a nonblank canonical provider comment
  ID is confirmed and the card rewrite succeeds.
- Provider comment IDs are issue-scoped unless official documentation promises
  stronger uniqueness. All reconciliation keys include provider, project,
  issue, and local draft identity.
- A refresh bypasses only the selected issue thread's cache. Unrelated cached
  threads remain warm.
- Threads are loaded lazily. Opening a 20+ card board must not issue one comment
  request per card.
- Every collection is completely paged, guarded against repeated/missing
  cursors and malformed/truncated envelopes, then normalized chronologically.
- Provider HTML, ADF, rich fragments, deleted records, replies, bot authors,
  external authors, null authors, and opaque IDs are treated as data, not
  markup or terminal control sequences.
- Secrets never enter fixtures, Markdown, cache keys, screenshots, logs, error
  text, or assertion output.
- One workspace sync owns an advisory lock. Concurrent UI/CLI sync attempts
  cannot duplicate a comment write.

## Actors and surfaces

| Actor or surface | Responsibility |
|---|---|
| Provider API | Canonical remote thread and append result |
| Provider adapter | Auth, route/query, pagination, wire validation, body conversion, error typing |
| Neutral tracker model | Lossless common comment identity, authorship, time, body, reply, deletion, URL |
| Markdown parser/renderer | Durable separation of remote snapshots, local drafts, source body, and private notes |
| Planner/outbound/journal | Exact preview identity, confirmation, at-most-once write handling, held recovery |
| ProviderBackend | Lazy per-card comment access, local draft persistence, refresh, cache scoping |
| Split/Rows/Kanban UI | Literal rendering, counts, scrolling, local composer, explicit refresh and sync state |
| CLI | Equivalent preview, confirmation, retry-held controls, and noninteractive safety |

## State model

```text
REMOTE_SNAPSHOT
    | add locally
    v
LOCAL_DRAFT --edit--> LOCAL_DRAFT --delete--> CANCELLED
    | confirmed Sync
    v
JOURNALED_ATTEMPT
    | definite reject             | response lost/malformed/ambiguous
    v                             v
LOCAL_DRAFT_RETRYABLE             HELD_FOR_OPERATOR
    | canonical nonblank result
    v
CONFIRMED_REMOTE
    | atomic Markdown rewrite + snapshot save
    v
REMOTE_SNAPSHOT_WITHOUT_DRAFT
```

No UI close, process crash, cache failure, Git failure, reload failure, or
progress observer failure may skip or invent a transition.

## Independent verification lanes

### Lane A: local Markdown, app, and sync behavior

This lane uses deterministic providers and real filesystem/TUI components. It
does not inspect provider implementation details.

1. Markdown round trips:
   - zero, one, 20, 21, 100, and multiline comments;
   - zero, one, and multiple drafts;
   - opaque IDs, duplicate IDs, duplicate IDs across cards, blank IDs;
   - Unicode, CJK, emoji, combining marks, CRLF, fenced code, YAML delimiters,
     reserved marker-looking lines, C0/C1 controls, and very long bodies;
   - deleted/tombstone comments, replies, null author/time/URL;
   - legacy files remain byte-identical when comments are absent;
   - remote snapshots rewritten from provider truth while private notes and
     local drafts remain byte-identical.
2. Local editing:
   - composer saves only Markdown and updates the pending count;
   - edit draft body, blank rejection, delete/cancel, two drafts on one card;
   - switch card/tab/layout, resize, restart app, and reopen workspace;
   - invalid Markdown visibly fails closed without losing recoverable text.
3. Sync planning:
   - comments appear exactly once in preview with the latest draft body;
   - preview identity changes if a draft changes after preview;
   - send, pull-only, decline, provider-choice conflicts, and stale plan;
   - comment operations coexist with create/edit/move operations on the same
     card without losing any field.
4. At-most-once outbound safety:
   - journal write failure before POST means zero POSTs;
   - timeout/disconnect/5xx/malformed or blank accepted response means one POST
     and held state across restart;
   - definite 400/401/403/404 rejection remains retryable and retains draft;
   - accepted response followed by Markdown, snapshot, Git, reload, or UI
     refresh failure never reposts;
   - explicit ambiguous retry is required and visible;
   - observer/progress callback failure cannot change write count.
5. TUI/CLI behavior:
   - Split stays inline; Rows/Kanban use the detail screen;
   - remote and pending styles, author/time/body, counts, empty/loading/error,
     read-only permission, keyboard/mouse, compact scrolling, all themes;
   - Sync progress includes comment hydration and outbound comment fractions;
   - active sync blocks duplicate F5, quit, project switch, edit, and palette;
   - terminal success/held/failure persists until acknowledgement.

### Lane B: provider API source-of-truth

This lane starts from current official documentation and tests provider-local
clients with MockTransport or captured redacted fixtures. It does not depend on
Markdown or TUI implementation.

For each provider verify:

- exact API version, base origin, HTTP method, route or GraphQL operation;
- auth header type and read/write scope or permission requirements;
- issue/card identifier rules, including custom IDs and workspace/team context;
- pagination parameters, limits, cursors, ordering, repeated-cursor guard, and
  malformed/missing envelope failure;
- comment-only filtering so system history is never presented as discussion;
- native body representation: Markdown, plain text, HTML, ADF, or rich parts;
- safe outbound conversion and escaping with no string-built JSON/GraphQL;
- canonical ID, author variants, timestamps, deletion, reply/parent identity,
  visibility, and permalink when actually available;
- 200/201/204 handling and strict schema validation;
- 400, 401, 403, 404, 409, 422, 429, timeout, disconnect, 5xx, partial GraphQL
  data/errors, and malformed success responses;
- a non-idempotent POST is attempted once, including read-back failures;
- issue-scoped cache invalidation and no credential/account cache crossover;
- rate-limit headers and retry behavior never replay a comment POST.

The lane produces a nine-row evidence table linking each assertion to official
documentation and the exact adapter test that enforces it.

## Cross-lane differential matrix

For every provider, use at least 20 cards and at least one card with 20+ thread
entries. The same canonical fixture must pass this sequence:

1. Feed native provider pages into the real adapter.
2. Assert the neutral chronological `RemoteComment` sequence.
3. Render it to a real Markdown card and parse it again.
4. Load the workspace through `ProviderBackend`.
5. Inspect the real Split and popup Comments surfaces.
6. Add a local draft and verify no transport write occurred.
7. Generate and confirm the real Sync plan.
8. Capture the exact provider-native create request.
9. Return a canonical provider response and, where required, read it back.
10. Rewrite/reload Markdown and compare neutral state with direct API truth.

Required card/thread variants are distributed deterministically across the
20+ dataset:

- empty, one-entry, 20-entry, and multi-page threads;
- duplicate display names but unique IDs;
- replies, deleted entries, bot/external/null authors;
- equal timestamps and out-of-order pages;
- missing optional URL/update time;
- Unicode and maximum practical body sizes;
- read-only and permission-limited cards;
- one malformed card/thread among otherwise valid data;
- simultaneous card edit, move, and comment draft;
- cached and explicitly refreshed threads.

## Live sandbox protocol

Offline contract tests are mandatory and sufficient for normal CI. Live tests
are an additional source-of-truth audit, never an implicit CI step.

Live reads require configured credentials and a dedicated sandbox project.
Live writes additionally require an explicit `PYKANTUI_LIVE_WRITES=1`-style
gate and user confirmation for the named provider/project. Every created
comment uses a unique nonsecret marker containing provider, run ID, and test
case. Because pykantui deliberately does not delete provider comments, the
plan must disclose that these audit comments may remain permanently.

For each authorized provider:

1. preflight identity, project, permissions, API version, and rate-limit headroom;
2. read one known thread directly through the provider client;
3. create exactly one marked comment through pykantui Sync;
4. read the thread directly again, bypassing cache;
5. compare canonical ID/body/author/time/parent/URL with Markdown and TUI;
6. rerun Sync and prove zero duplicate writes;
7. retain a redacted receipt containing IDs and hashes, never tokens or bodies
   that may contain private data.

If a provider lacks a safe sandbox or explicit authorization, record the live
gate as `NOT RUN`, not `PASS`. Never test writes against an ordinary customer
project merely because credentials happen to exist.

## Performance and concurrency gates

- Opening a board with 20, 100, or 1,000 cards performs zero comment reads.
- Opening one Comments tab performs at most one logical thread read; reopening
  uses cache; explicit refresh bypasses only that issue.
- A 20+ page/thread read performs exactly the documented request count.
- Comment rendering remains vertically scrollable with no horizontal overflow
  at 80x24 and the supported normal terminal sizes.
- Twenty concurrent local draft saves serialize safely without corrupting a
  card file.
- Two sync processes produce one lock winner and zero duplicate POSTs.
- Detached comment panes, entries, workers, and timers are collectible after
  repeated open/close/card-switch cycles.

## Security and data-integrity gates

- No secret reaches logs, exceptions, snapshots, Markdown, cache namespaces,
  screenshots, subprocess arguments, or test output.
- Workspace `.env` cannot redirect an exported token to an untrusted origin.
- Custom/self-hosted provider origins remain credential-bound and HTTPS-only.
- Provider HTML/ADF is converted as data; no terminal/Rich markup execution.
- Symlinked cards/columns and path traversal never become sync inputs.
- Cache files and pending journals use private permissions and atomic writes.
- Authorization errors preserve local drafts and reveal no credential details.
- Malformed provider success responses preserve the last complete local thread.

## Test organization and execution order

1. Pure model/schema/payload/mapper tests.
2. Markdown parser/renderer and journal state-machine tests.
3. Provider MockTransport/GraphQL pagination and failure matrices.
4. Filesystem sync integration tests with exact call counters.
5. Real Textual user journeys and lifecycle/memory checks.
6. Cross-provider 20+ differential tests.
7. Network-disabled Ubuntu Docker repetition.
8. Full Ruff, strict mypy, branch coverage (minimum 80% on changed production
   modules), secret scan, dependency audit, whitespace/diff review.
9. Optional live sandbox reads/writes under the explicit protocol.
10. Independent final audit comparing the two lanes and rejecting unsupported
    claims.

## Exit criteria

The goal is complete only when:

- every shipped provider has an official-contract evidence row and executable
  read/create transport tests;
- every provider passes the same neutral Markdown/app/sync differential flow;
- every tested failure leaves an exact, documented local/journal state and
  proves the expected provider write count;
- 20+ card/thread pagination, caching, UI scrolling, and no-N+1 assertions pass;
- offline Docker, static, coverage, security, and diff gates are green;
- any live provider not exercised is clearly marked `NOT RUN` with the reason;
- no unresolved P0/P1 integrity finding remains.

## Non-goals

- Editing or deleting an already remote provider comment.
- Pretending all providers expose identical reply, visibility, HTML, URL, or
  author semantics.
- Running destructive cleanup or permanent writes in customer projects.
- Treating mocked HTTP as proof that a live credential has permission.
- Treating a successful live call as proof of failure recovery or at-most-once
  safety; those remain deterministic offline tests.

## Open decisions

- Which dedicated sandbox project/workspace is authorized for each provider?
- Which providers, if any, have explicit approval for permanent marked test
  comments during this run?
- Should nested replies be flattened chronologically in the neutral UI or
  retain parent indentation when the provider supplies it?
- What maximum comment body size should the UI enforce before provider-specific
  validation, and should that be the minimum limit across providers or a
  provider-aware limit?
- Should a runtime scope probe hide the composer for read-only tokens, or should
  static capability plus a clear Sync-time permission error remain the policy?

## Handoff

The capability contract is ready for TDD execution in the two independent
lanes above. Offline verification can proceed immediately. Live write coverage
requires the named sandbox and explicit authorization decisions; it must not
block truthful completion of the offline contract matrix.
