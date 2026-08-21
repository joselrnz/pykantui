# Release privacy audit

This ledger tracks the working-tree cleanup that separates public pykantui
artifacts from a developer's personal identity. It deliberately records
categories and paths, not the removed identity values.

## Scope

- Public package metadata, license attribution, README links, and generated
  workspace copy.
- Tracked tests and screenshot generators.
- README PNG/GIF metadata and visible provider fixtures.
- Untracked local MCP probes and runtime logs.
- Current tracked and unignored files checked by the privacy and secret gates.

Git commit authorship and repository hosting are outside this reversible
working-tree change. Changing either requires an explicitly approved history
rewrite or repository transfer.

## Change ledger

| Category | Paths | Change |
| --- | --- | --- |
| Attribution | `LICENSE`, `pyproject.toml` | Use the project-level `pykantui contributors` identity and a neutral documentation URL. |
| Documentation | `README.md` | Replace account-bound URLs with repository-relative links. |
| Generated workspaces | `src/pykantui/commands/init.py` | Remove the account-bound link from generated Markdown. |
| Fixtures | `tests/unit/commands/test_onboarding.py`, `tests/unit/tracker/test_tracker.py` | Replace personal sample identities with synthetic people. |
| Visual generators | `tools/*screenshot*.py` | Replace personal users and account handles with synthetic fixtures. |
| MCP tooling | `tools/mcp_socket_smoke.py` | Resolve the repository from the script location or an environment override. |
| Local containment | `.gitignore` | Ignore disposable MCP probe scripts and server logs that may contain machine paths. |
| Privacy gate | `tools/privacy_scan.py`, `tests/unit/tools/test_privacy_scan.py` | Scan tracked and unignored text plus image metadata without echoing matched identities. |
| Public fixture source | `tools/provider_evidence.py` | Use nine provider-specific enterprise projects and 27 synthetic cards per provider. |
| GIF assembly | `tools/readme_provider_gifs.py`, `tests/unit/tools/test_readme_provider_gifs.py` | Build complete provider journeys from validated compositor captures with metadata stripped. |
| README media | `assets/live-real-9x1*.gif`, `assets/demo-providers-timeline.gif` | Replace account-derived captures with synthetic Textual captures. |

## Visual asset contract

Each provider journey contains nine user-visible phases: board, local create,
Markdown edit, TUI edit, move, comment draft, sync result, source-of-truth
validation, and conflict review. Identical adjacent frames may be coalesced by
GIF encoding while retaining their combined duration.

- Providers: 9
- Cards per provider: 27, plus one local draft during the journey
- Source captures: 81 SVG + 81 PNG
- Source geometry: 1726 x 1026 pixels; zero mismatches
- Provider GIF duration: 13.5 seconds each
- Provider GIF metadata: no author or source-path fields
- Network and credentials: forbidden during capture

The ignored local evidence manifest is
`artifacts/provider-evidence-public/public-readme-privacy-20260820b/manifest.json`.

## Verification commands

```powershell
$env:PYTHONPATH = 'src;.'
python -m unittest tests.unit.tools.test_privacy_scan tests.unit.tools.test_readme_provider_gifs tests.unit.tools.test_provider_evidence
python tools/secret_scan.py .
python tools/privacy_scan.py . --deny '<private-name>' --deny '<private-account>' --deny '<private-email>'
python -m ruff check tools/privacy_scan.py tools/readme_provider_gifs.py tools/provider_evidence.py tests/unit/tools
python -m mypy tools/privacy_scan.py tools/readme_provider_gifs.py tools/provider_evidence.py tests/unit/tools/test_privacy_scan.py tests/unit/tools/test_readme_provider_gifs.py tests/unit/tools/test_provider_evidence.py
git diff --check
```

The privacy command reports only a relative path and finding category. It does
not print the denied value.

## Remaining external association

The current Git remote and existing commit author metadata remain associated
with the original personal account. A clean working tree cannot change that.
An anonymous public release therefore additionally requires a neutral hosting
organization and an explicitly approved author-history rewrite.
