# The issue file format

One issue is one markdown file. This is the contract between pykantui, your
editor, and git — so it is written down, versioned, and enforced by tests
rather than left to whatever the code happened to emit.

## The whole file

```markdown
---
key: JPT-4
id: "10018"
provider: jira
title: Task 1
status: In Progress
column: in-progress
type: Task
priority: High
assignee: alex@acme.com
reporter: sam@acme.com
labels: [backend, urgent]
components: [API, Platform]
parent: JPT-1
created: 2026-08-07T20:56:05-05:00
updated: 2026-08-07T20:57:10-05:00
due: 2026-08-09
url: https://acme.atlassian.net/browse/JPT-4
---

# JPT-4 · Task 1

<!-- pykantui:source — from the tracker, rewritten on every sync -->

The description as the tracker holds it.

<!-- pykantui:comments — from the provider, rewritten on refresh -->

<!-- pykantui:comment id="20031" issue="10018" author="Sam" created="2026-08-08T09:15:00-05:00" -->
This comment came from the provider.
<!-- pykantui:comment-end id="20031" -->

<!-- pykantui:comment-drafts — yours until a confirmed sync sends them -->

<!-- pykantui:comment-draft id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA" issue="10018" created="2026-08-13T12:30:00-05:00" -->
This reply is still local.
<!-- pykantui:comment-draft-end id="comment-01J5KX7K9Z8F2N4Q6P3S1T0VWA" -->

<!-- pykantui:notes — yours, never touched by a sync -->

Whatever you write here survives forever.
```

## File regions and ownership

| Region | Owner | On sync |
|---|---|---|
| Frontmatter | the tracker, except the editable fields below | rewritten |
| Heading | pykantui | rewritten |
| `pykantui:source` block | tracker description, locally editable | sent only after Sync confirmation; then rewritten from the provider response |
| `pykantui:comments` block | provider discussion | refreshed for opted-in cards; local edits are never sent |
| `pykantui:comment-drafts` block | you | sent as new comments only after Sync confirmation |
| `pykantui:notes` block | you | **never touched** |

The `notes` marker is the single most important line in the file. Without it,
the second sync would silently eat whatever you typed, and nobody would trust
the tool again.

## Frontmatter

**Order is fixed**, so that files diff cleanly and a human learns where to look:
identity, then state, then people, then dates, then links.

1. `key`, `id`, `provider`
2. `title`, `status`, `column`, `type`, `priority`
3. `assignee`, `reporter`, `labels`, `components`, `parent`
4. `created`, `updated`, `due`
5. `url`

**Empty fields are omitted.** A block full of `priority:` with nothing after it
is noise, and it makes "no priority" indistinguishable from "priority was never
set by this tracker".

### Types

| Field | Type | Note |
|---|---|---|
| `id` | quoted string | Always quoted. It is an opaque identifier, and `007` must not become `7`. |
| `key` | string | The human identifier. Bare unless it needs quoting. |
| `labels`, `components` | list of strings | Flow style `[a, b]` — short, and it diffs on one line. |
| `created`, `updated` | ISO 8601 with `T` and an offset | `2026-08-07T20:56:05-05:00` |
| `due` | `YYYY-MM-DD` | A due date is a day, not an instant. |
| everything else | string | |

Dates are written as **strings**, not as YAML's native timestamp type. PyYAML
would otherwise emit `2026-08-07 20:56:05.516000-05:00` — a space instead of a
`T`, and microseconds nobody asked for — which is valid YAML but not ISO 8601,
and is not what any other tool reading these files will expect.

### `status` vs `column`

They are not duplicates.

- `status` is what the **tracker** calls it: `In Progress`.
- `column` is the **folder** the file is in: `in-progress`.

They differ whenever the column style slugifies, and on trackers where several
statuses map onto one board column. `column` is written *from the file's path*,
never read back as authority.

### Which fields you may edit

Editing anything else is harmless but pointless — it is overwritten on the next
sync. These are read back and pushed:

`title` · `type` · `assignee` · `labels` · `components` · `due` · `priority`

Plus the **provider description above the notes marker**, and the **folder the file sits in**.
Moving the file between column folders is how you move a card; that is why
`column` in the frontmatter is not the authority.

These edits are local until an explicit Sync preview is confirmed. The preview
lists the exact provider fields to send. Text below `pykantui:notes`, local Git
history, credentials, and cache files are never sent to a provider. Jira is the
only built-in provider that accepts `components`; another provider holds that
field locally and reports it as unsupported instead of guessing a mapping.

**Deleting a line clears that field.** The writer emits `assignee`, `labels`,
`due` and `priority` whenever the tracker has a value, so a key that is gone
was removed by hand — and a confirmed sync sends an explicit null. The one
exception is `title`: a card with no title is not something anyone means to
ask for, so a missing title reads as "leave it alone".

What each tracker will actually accept differs — see `writable_fields` in each
provider's spec. An edit to a field a tracker cannot write is reported, not
silently dropped.

## Comments

Provider comments and local replies are deliberately separate. A provider
comment is a snapshot of remote history. Editing or deleting that snapshot in
Markdown never edits or deletes the remote comment; the next successful
refresh restores the provider version.

To write a reply, use the Comments tab or add text inside a
`pykantui:comment-draft` record. Saving the draft changes only the Markdown
file. The next explicit Sync preview lists a `COMMENT` operation and shows the
comment body. The provider receives it only after that preview is confirmed.
Declining, running a pull-only refresh, losing permission, or getting a
provider error leaves the draft intact. If a provider accepted the write but
its response was lost or malformed, the draft is held to prevent an automatic
duplicate. After checking the provider, an operator may deliberately retry it
with `kbn sync --retry-ambiguous-comments`.

An unsent draft may be edited in Markdown; its latest body is the one shown in
the preview. Deleting the entire draft record cancels that pending comment.

Comment creation is append-only. pykantui does not currently edit or delete
existing provider comments. A content-free pending journal records an
in-flight create before the request is sent. If the provider may have accepted
the request but the response is lost, pykantui holds the draft instead of
blindly posting it again and creating a duplicate.

Comments are loaded per card when its Comments tab is opened or refreshed.
Ordinary board loading does not fetch every thread, avoiding one API request
per card. An explicit Refresh bypasses the selected thread's cache without
refetching the whole project. The provider cache for that one thread is
invalidated after an attempted create; unrelated cached threads stay warm.

## The heading

`# JPT-4 · Task 1`

Present so the file reads as a document in an editor preview or on a git host,
rather than as a frontmatter blob with loose text under it. Regenerated from
the frontmatter, so editing it does nothing — change `title` instead.

## The markers

```
<!-- pykantui:source — from the tracker, rewritten on every sync -->
<!-- pykantui:comments — from the provider, rewritten on refresh -->
<!-- pykantui:comment id="..." ... -->
<!-- pykantui:comment-end id="..." -->
<!-- pykantui:comment-drafts — yours until a confirmed sync sends them -->
<!-- pykantui:comment-draft id="..." ... -->
<!-- pykantui:comment-draft-end id="..." -->
<!-- pykantui:notes — yours, never touched by a sync -->
```

HTML comments, so they are invisible in any rendered view but obvious in the
raw file. Each comment record has explicit start and end markers so multiline
Markdown, fenced code, YAML delimiters, Unicode, and provider-generated text
round-trip safely. Reserved marker-looking lines inside a comment are escaped
reversibly. ANSI escapes and terminal control characters are stripped.

A file with **no** notes marker is read as having no notes — not as "the whole
body is notes". The alternative would freeze that file against every future
sync.

## Robustness rules

These are guarantees, each with a test:

- **Broken YAML never loses your notes.** An unparseable frontmatter block is
  read as empty; the notes below it are still recovered.
- **A file with no frontmatter still parses.**
- **A missing source marker is tolerated** — everything before the notes marker
  is treated as tracker-owned.
- **Legacy cards stay byte-identical.** A card with no comment thread or draft
  gets no empty comment regions merely because comments support exists.
- **Malformed or duplicate comment records fail closed.** They are reported and
  never interpreted as outbound drafts.
- **Provider comment history is not outbound data.** Only records under the
  comment-drafts marker can produce a create operation.
- **Titles are collapsed to one line.** A newline in a YAML scalar would break
  the block.
- **Filenames never carry a space**, and neither do column folders. See
  `ColumnStyle`.
- **Rewriting an unchanged issue produces a byte-identical file**, so a sync
  that found nothing makes no commit.
