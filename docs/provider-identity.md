# Identity and people, per provider

Everything needed to answer three questions for each tracker:

1. **Who am I?** — so "my issues" can mean something.
2. **Who else is there?** — the people directory that gets cached.
3. **Can the server filter to mine?** — or must we fetch all and filter here.

Every row is either **measured against a live instance** or **taken from the
vendor's own documentation**, and which one is marked. Nothing here is inferred
from how a similar API behaves.

| mark | meaning |
|---|---|
| ✅ | run against a live instance in this repo |
| 📄 | from the vendor's documentation, not yet run |
| ❓ | could not confirm from the docs |

---

## Summary

| Provider | Identity endpoint | Gives a person? | Email guaranteed? | Server-side "mine"? |
|---|---|---|---|---|
| Jira ✅ | `GET /rest/api/3/myself` | yes | **no** — privacy-dependent | **yes**, `currentUser()` |
| Plane ✅ | **none** | **no** — workspace-scoped key | yes, in the member list | **no** — filters ignored |
| Forgejo 📄 | `GET /api/v1/user` | yes | **no** — nullable | yes, `assigned_by=` / `created_by=` |
| GitHub 📄 | `GET /user` | yes | **no** — nullable | yes, `assignee=` / `creator=` |
| Linear 📄 | `query { viewer { … } }` | yes | yes | yes, filter by `assignee.email` |
| Asana 📄 | `GET /users/me` | yes | yes | via `assignee` on task search |
| ClickUp 📄 | `GET /v2/user` | yes | yes | `assignees[]` on list tasks |
| Shortcut 📄 | `GET /api/v3/member` | yes | yes | search syntax `owner:` |
| Trello 📄 | `GET /1/members/me` | yes | yes | **no** — filter client-side |
| Monday 📄 | `query { me { … } }` | yes | yes | ❓ not confirmed |

**The two that break the pattern are the two I could actually test.** That is
not a coincidence — it is what running things reveals.

---

## Jira ✅ measured

**Identity** — `GET /rest/api/3/myself`

```json
{ "accountId": "712020:0a1b2c3d-4e5f-6789-abcd-ef0123456789",
  "displayName": "alex",
  "emailAddress": "alex+jira@example.com" }
```

`emailAddress` came back here because it is *your own account on your own
site*. Atlassian's privacy settings omit it for most other users, and an app
needs a specific scope to see it at all. **Do not depend on it.**

**People** — `GET /rest/api/3/user/assignable/search?project=JPT`
Measured: 1 assignable user, with `accountId`, `displayName`, `emailAddress`,
`active`.

**Mine, server-side** — JQL `currentUser()`. Measured on JPT:

| JQL | issues |
|---|---|
| `project = JPT` | 5 |
| `project = JPT AND assignee = currentUser()` | 2 |
| `project = JPT AND reporter = currentUser()` | 5 |
| `project = JPT AND (assignee = currentUser() OR reporter = currentUser())` | 5 |

`currentUser()` resolves server-side, so Jira needs no identity lookup at all
to answer "mine".

**Ids on issues** — `fields=assignee,reporter` returns `accountId` on both.
We currently keep only `displayName` and throw the ids away.

---

## Plane ✅ measured — the outlier

**Identity — there isn't one.** An API key is scoped to a *workspace*, not a
person. There is no `/me`. `GET /workspaces/{slug}/members/` returns everyone,
bot first.

> This is why identity must be configurable. On Plane, `me` has to be set
> explicitly or "mine" cannot be computed at all.

**People** — `GET /api/v1/workspaces/{slug}/projects/{id}/members/`

Returns a **bare JSON list** — no `results`, no cursor — unlike every other
Plane collection. Measured, 2 members:

```json
[{ "id": "9f8e7d6c-…", "display_name": "alex",
   "email": "alex@example.com", "is_bot": false, "is_active": true },
 { "id": "f0bd2b18-…", "display_name": "Plane",
   "email": "bot_user_74475718-…@plane.so" }]
```

Fields: `id`, `email`, `display_name`, `first_name`, `last_name`, `avatar`,
`is_active`, `is_bot`, `role`, `role_slug`.

**A bot is in the directory** and must be excluded from assignment.

**Mine, server-side — no.** Measured: `?assignees={uuid}` and
`?created_by={uuid}` both returned `total_count: 7`, identical to no filter.
The parameters are accepted and ignored on this endpoint.

**Ids on issues** — `assignees` is an array of member UUIDs; `created_by` is a
UUID.

---

## GitHub 📄

**Identity** — `GET /user` → `id`, `login`, `email`, `name`.
`email` is **nullable** — present in the schema, often null in practice.
`login` is the reliable identity, and it is also the public handle.

**People** — `GET /repos/{owner}/{repo}/assignees` → array of Simple User.

**Mine, server-side** — `GET /repos/{owner}/{repo}/issues` accepts:

| param | meaning |
|---|---|
| `assignee` | a username; `none` for unassigned, `*` for any |
| `creator` | the user who opened it |
| `mentioned` | a user mentioned in it |
| `state` | `open` / `closed` / `all` |
| `since` | ISO 8601 — the incremental-fetch hook |

`filter=assigned/created/mentioned` exists on `/issues` and `/user/issues`,
**not** on the repository endpoint.

---

## Forgejo 📄

**Identity** — `GET /api/v1/user` → `id`, `login`, `full_name`, `email`.
Pykantui uses `login` as the stable assignment handle because Forgejo's issue
write API accepts assignee login names, while email may be absent.

**People** — assignees are embedded in issue responses. Forgejo also exposes
repository collaborators and issue-assignee endpoints when a picker needs a
complete directory.

**Mine, server-side** — `GET /api/v1/repos/{owner}/{repo}/issues` accepts
`assigned_by`, `created_by`, and `mentioned_by`, plus `type=issues` to exclude
pull requests. Pykantui currently fetches repository issues and applies its
shared identity filter locally so cache and sync see the same complete set.

---

## Linear 📄

**Identity**

```graphql
query Me { viewer { id name email } }
```

**People** — `query { users { nodes { id name email } } }`

**Mine, server-side** — filtering by relationship, and **email works
directly**:

```graphql
issues(filter: { assignee: { email: { eq: "john@linear.app" } } })
```

There is also `viewer { assignedIssues { nodes { … } } }` and
`viewer { createdIssues { … } }`, which is the cleanest "mine" of any tracker
here — no id lookup and no filter syntax.

Linear's docs explicitly recommend filtering server-side to stay inside the
rate limit.

---

## Asana 📄

**Identity** — `GET /users/me`. The literal string `me` is accepted anywhere a
user gid is expected.

Returns `gid`, `email`, `name`, `workspaces`. Requires the `users:read` scope.

**People** — `GET /users?workspace={gid}`, and project membership via the
project's members.

**Mine** — the task search endpoint takes `assignee`; `me` is valid there too.

---

## ClickUp 📄

**Identity** — `GET /v2/user`

```json
{ "user": { "id": 123, "username": "John Doe", "email": "user@company.com" } }
```

Note the response is **wrapped in a `user` key** — the id is an integer.

**People** — `GET /v2/list/{list_id}/member`.

**Mine** — `GET /v2/list/{id}/task?assignees[]={id}`, plus `date_updated_gt`
for incremental fetches.

---

## Shortcut 📄

**Identity** — `GET /api/v3/member`

Confirmed from Shortcut's own JavaScript client, whose `getCurrentMemberInfo()`
is documented as calling `GET:/api/v3/member`. The Python client exposes the
same as `get_current_member()`.

❓ **The Member schema and the list-members path could not be confirmed** — the
docs site returns only its Categories/Custom Fields/Documents/Epics sections to
a fetch, and the members section never rendered. Our code currently reads
`id`, `mention_name`, `name`; that needs checking against a real response
before it is trusted.

**Mine** — search syntax, `owner:`.

---

## Trello 📄

**Identity** — `GET /1/members/me`

Trello interprets `me` in place of a member id as "the user this token belongs
to". `/1/members/me` and `/1/member/me` are equivalent.

Returns `id`, `username`, `fullName`, `email`, plus `idBoards`,
`idOrganizations` and preferences.

**People** — `GET /boards/{id}/members`. The docs page confirms the path but
publishes no response schema.

**Mine — no server-side filter.** Cards must be fetched for the board and
filtered on `idMembers` locally.

> Security note from Atlassian's own docs, worth repeating: a Trello *token*
> grants access to the whole account. The *key* may be public; the token must
> not be.

---

## Monday 📄

**Identity**

```graphql
query { me { id name email } }
```

Requires the `me:read` scope. **`me` can only be queried at the root** — it
cannot be nested inside another query, which matters when batching.

Also available: `is_guest`, `is_admin`, `created_at`.

**People** — `query { users { id name email } }`.

**Mine** — ❓ not confirmed. Monday's people column holds user ids, so a
client-side filter on the column value is the safe assumption until tested.

---

## What this changes in the plan

**1. Our ten `verify()` implementations are all correct.** Every path in the
code matches what the docs or the live instances say — `/rest/api/3/myself`,
`/user`, `/api/v1/user`, `/api/v3/member`, `/1/members/me`, `viewer`, `me`,
`/users/me`, `/v2/user`. Nothing to fix there.

**2. Identity must be optional-but-configurable, because of Plane.** Nine
providers hand us a person. Plane cannot. The design has to be "config wins,
else ask the provider, else fail with a message naming what to set" — and that
is driven by one real tracker, not by caution.

**3. Email cannot be the universal key.** Guaranteed on Plane, Linear, Asana,
ClickUp, Trello, Monday. **Not** guaranteed on Jira (privacy) or GitHub
(nullable). The chain has to be `provider id → email → username`, and for
GitHub the `login` is the better thing to put in a file anyway.

**4. Client-side filtering is the right default.** Two of the ten cannot filter
server-side at all — Plane ignores the parameters, Trello has none — and we
fetch everything for the cache regardless. Server-side stays an optimisation,
with Jira's `currentUser()` and Linear's `viewer.assignedIssues` the two
strongest cases.

**5. Three things need verifying against a real instance** before being
trusted: Shortcut's Member schema, Monday's mine-filter, and Trello's board
member response shape.

---

## Sources

- [GitHub — Users](https://docs.github.com/en/rest/users/users?apiVersion=2022-11-28) · [Issues](https://docs.github.com/en/rest/issues/issues?apiVersion=2022-11-28) · [Assignees](https://docs.github.com/en/rest/issues/assignees?apiVersion=2022-11-28)
- [Forgejo — API usage](https://forgejo.org/docs/latest/user/api/usage/) · [Access token scope](https://forgejo.org/docs/latest/user/authentication/token-scope/) · [OpenAPI reference](https://codeberg.org/swagger.v1.json)
- [Linear — GraphQL](https://linear.app/developers/graphql) · [Filtering](https://linear.app/developers/filtering)
- [Asana — Get a user](https://developers.asana.com/reference/getuser)
- [ClickUp — Get authorized user](https://developer.clickup.com/reference/getauthorizeduser)
- [Monday — me](https://developer.monday.com/api-reference/reference/me)
- [Trello — Members](https://developer.atlassian.com/cloud/trello/rest/api-group-members/) · [Boards](https://developer.atlassian.com/cloud/trello/rest/api-group-boards/) · [Authorization](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)
- [Shortcut — REST API v3](https://developer.shortcut.com/api/rest/v3) · [JS client](https://useshortcut.github.io/shortcut-client-js/)
- Jira and Plane: measured against `acme.atlassian.net` and workspace `acme`, August 2026.
