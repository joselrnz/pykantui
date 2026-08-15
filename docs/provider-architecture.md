# Provider architecture

Each integration follows the same boundary without pretending the services
have the same API:

```text
providers/<name>/
  __init__.py   exports the provider class for registry discovery
  client.py     authentication and service-required headers
  fields.py     typed card/edit/create field contract
  schemas.py    pydantic models for the response fragments pykantui consumes
  mapper.py     pure conversion from wire models to neutral tracker models
  payloads.py   pure construction of create and update request bodies
  routes.py     REST route construction, for REST providers
  operations.py GraphQL documents, only for GraphQL providers
  provider.py   provider-neutral orchestration
```

REST providers keep endpoint paths in `routes.py`; GraphQL documents are
larger and reusable across list, refresh, create, and edit paths, so Linear
and Monday.com keep them in `operations.py` and have no `routes.py`. Empty
files are deliberately not created: a boundary exists when a provider has
enough behavior to need one.

The shared `pykantui.api` package is provider-agnostic. It owns:

- one pooled `httpx.Client` transport;
- normalized authentication, not-found, rate-limit, and transport errors;
- response caching and conditional revalidation;
- cursor, token, offset, and page-number generators;
- a bounded retry policy.

Authenticated redirects are refused rather than followed, provider error text
is stripped of credentials and terminal control sequences, and pagination
fails closed on repeated cursors or its audited page limit.

Retries are limited to safe reads (`GET`, `HEAD`, and `OPTIONS`). Provider
writes are not replayed automatically: a create that times out may already
exist remotely, so retrying it could duplicate a card. A content-free journal
blocks that draft until the provider has been checked; only the explicit
`--retry-ambiguous-creates` option retries it.

## Sync and local history

Provider sync and Git are separate systems:

1. Read Markdown and the last provider snapshot.
2. Build a provider-write plan and check edited cards for remote changes.
3. Create a local Git checkpoint before any approved provider write.
4. Send approved creates, edits, moves, and append-only comment drafts to the provider.
5. Refresh directly from the provider, bypassing the issue cache.
6. Rewrite Markdown from what the provider accepted.
7. Save the new snapshot and create an after-sync local checkpoint.

Pykantui exposes no Git remote, fetch, pull, or push operation. `.git` is an
on-device recovery history for that workspace. The explicit `kbn sync` action
is what communicates with Jira, GitHub, Asana, and the other providers.

## Comment threads

All built-in providers implement the same neutral comment boundary while
retaining their native wire format and pagination:

- `iter_comments(project_id, issue)` yields a complete chronological thread;
- `create_comment(project_id, issue, draft)` appends one comment and returns
  the provider's canonical ID plus provider-supplied author, timestamps, and
  permalink when that service exposes them;
- `read_comments` and `create_comments` declare provider features separately.
  A token that lacks the service's write permission may still be rejected at
  Sync time; in that case the local draft is preserved.

Threads are issue-scoped and lazy. Opening a Comments tab reads the cached
Markdown thread; Refresh bypasses the selected thread's cache and rewrites only
that card. Board loading never fetches comments for every card. Successful
creates invalidate only the affected thread.

A new comment is first an append-only local Markdown draft. Sync includes it
in the confirmation identity and writes a content-free
`.pykantui/pending-comments.json` record before POSTing. Comment POSTs are
never retried automatically: a lost response is ambiguous and replaying it
could duplicate the discussion entry. Provider-owned comment records can be
read and refreshed, but pykantui does not currently edit or delete them.

Only one process may sync a workspace at a time. A cross-platform advisory
lock protects the Markdown tree, snapshot, and local Git index from
concurrent writers. Symlinked columns and Markdown files are ignored so a
provider-controlled path cannot make files outside the workspace into sync
inputs.

Provider responses are cached under
`~/.pykantui/cache/<provider>/<project-workspace>/`. The project/workspace
scope prevents two local workspaces with different credentials from sharing
responses. `~/.pykantui/projects.json` is an atomically updated, locked registry
that records the provider/project identity and canonical path chosen during
`kbn init`; it contains no provider configuration or credentials.

Malformed or type-invalid Markdown remains visible as `✗ invalid Markdown`,
but is excluded from outbound edits, overwrites, moves, and pruning until the
file is repaired.

Credentials are stored outside the workspace. Generated `.gitignore` files
also reject `.env`, `auth.json`, and `credentials.json` as defense in depth.
Credential records are bound to the exact canonical HTTPS origin. Custom
origins must be supplied explicitly before a token from the environment can be
used with them.
