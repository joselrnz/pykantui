# Declarative issue batches

`kbn batch` separates generation, local/AI editing, provider-aware planning,
and provider writes. A generated YAML file is inert data: loading it never
runs templates or contacts a tracker.

## Generate and review ten Jira issues

```powershell
kbn batch jira --count 10 -o issues.yml
```

The generator writes ten explicit stable refs (`issue-01` through
`issue-10`). Edit the title, description, type, parent relationship, labels,
and requested state in an ordinary editor:

```yaml
apiVersion: pykantui.dev/v1alpha1
kind: IssueBatch
metadata:
  name: checkout-release
target:
  provider: jira
  project: PAY
defaults:
  type: Story
  state: To Do
issues:
  - ref: checkout-story
    title: Add checkout recovery
    body: Recover a payment without creating a duplicate charge.
    state:
      name: Done
      via: [In Progress, Done]
  - ref: checkout-test
    title: Test checkout recovery
    type: Sub-task
    parent: checkout-story
```

Refs are local dependency names, not Jira keys. During apply, parents are
created first and a sub-task receives the confirmed Jira key of its parent.
Cycles, missing parents, invalid issue types, and non-sub-task children fail
during planning.

## Optional AI refinement

An AI assistant can produce a separate proposal. Importing it changes only a
new local YAML file; it cannot call Jira:

```yaml
apiVersion: pykantui.dev/v1alpha1
kind: IssueBatchRefinement
batch: checkout-release
issues:
  - ref: issue-01
    title: Add retry-safe checkout recovery
    body: Document acceptance criteria and duplicate-charge safeguards.
```

```powershell
kbn batch refine issues.yml --proposal ai-proposal.yml -o issues.refined.yml
```

By default AI may fill only missing fields. User-authored values and defaults
cannot be overwritten. Every accepted field is marked under `sources` as
`ai`; `--redo-ai` permits replacing only fields already marked `ai`. Review
the diff before planning.

## Plan without writes

Run this inside the matching pykantui workspace (or pass `--path`):

```powershell
kbn batch plan issues.refined.yml -o issues.plan.json
```

Planning reads live provider metadata and resolves the exact project, issue
types, states, parents, and transition route. It does not create or move any
issue. The saved JSON contains a source hash, exact operation signatures, a
short validity window, and its own tamper-detection hash. Editing either the
manifest or plan invalidates apply.

Jira workflows do not necessarily allow a direct jump between arbitrary
states. Supply `state.via` when multiple workflow transitions are required.
Each hop is executed and read back separately. A missing or rejected hop
stops the batch instead of guessing another route.

## Apply the exact reviewed plan

```powershell
kbn batch apply issues.plan.json
# automation must opt in explicitly:
kbn batch apply issues.plan.json --yes
```

Apply asks before making provider changes. Non-interactive use requires
`--yes`. A durable journal records `creating`, confirmed remote identity, and
every transition hop. Confirmed items are not recreated on retry. If a create
request's outcome is unknown, pykantui stops and requires manual provider
inspection rather than risk a duplicate.

Completed issues are written through the normal provider Markdown and sync
state formats. The local card records its `batch-id` and `batch-ref`, so later
`kbn sync` and MCP card edits keep the batch provenance.

## Current scope

The `jira` subcommand is a convenient ten-item generator. The manifest and
planner use the shared provider contract, but apply succeeds only when the
selected provider advertises every requested capability. Jira supports issue
creation, hierarchy, and state movement. Forgejo issue creation works through
normal Markdown sync; its Projects board card-position API is not available
in the tested Forgejo version, so declarative board-column movement remains
unsupported there.
