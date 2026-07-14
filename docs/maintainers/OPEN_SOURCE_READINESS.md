# Open Source Readiness

Publish this WQB research agent as a small, reproducible codebase free of private run state.

## Public Source

These paths may contain publishable source after review. The effective public allowlist is always `release/public_snapshot_manifest.json`; a path appearing below does not make every descendant publishable.

- `src/**`
- `schemas/**`
- `packages/**`
- `scripts/**`
- `tests/**`
- `docs/**`
- `.github/**`
- `configs/templates/**`
- `.local/research/workflows/**` only after a generic example is explicitly added to the manifest
- `.local/data/**/.gitkeep`

## Private Local State

These paths are local runtime outputs and should not be committed:

- `.env` and `.env.local`
- `*.pid`
- `*.log` and `.local/logs/**`
- `.local/data/runs/**`
- `.local/data/callbacks/**`
- `.local/data/memory/**`
- `.local/data/evaluations/**`
- `.local/data/registry/**` except `.gitkeep`
- `output/playwright/**`
- `.local/research/scans/continuous-alpha/**`
- `.local/research/workflows/continuous-alpha/runtime/**`

Real alpha expressions, scan recipes, submission records, account state, run outputs,
local memory stores, and private workflow configurations are private research assets even
when they exist outside one of the paths above. The complete owner decision is recorded in
[PUBLICATION_DECISIONS.md](PUBLICATION_DECISIONS.md).

## Release Audit

Run the complete release verification before staging a release:

```powershell
uv run python -m scripts.dev release-check --json
```

The release check includes the publish-candidate audit, which reads Git's tracked and
non-ignored untracked candidate set. It rejects private runtime artifacts, placeholder
repository metadata, real-looking credential assignments, and examples that enable live
submission by default. Credential values are never echoed in findings.

## History-Free Public Snapshot

Do not push the private research repository or reuse its Git history. Review the public allowlist first:

```powershell
.\.venv\Scripts\python.exe -m scripts.release.export_public_snapshot --workspace-root . --output dist/public-snapshot --check --json
```

Create a draft filesystem snapshot only after reviewing the selected paths:

```powershell
.\.venv\Scripts\python.exe -m scripts.release.export_public_snapshot --workspace-root . --output dist/public-snapshot --audit-output dist/public-snapshot-audit --json
```

The source of truth is `release/public_snapshot_manifest.json`. The source snapshot is
immutable after export; smoke builds run from a temporary copy. The sidecar audit directory
records source provenance, per-file SHA-256 values, and structured release blockers and is
not copied into the public repository. A snapshot with `publish_ready=false` must not be
initialized or pushed as the public repository.

Never publish `dist/release-check/public-snapshot` from an interrupted or older command.
Use the pristine snapshot reported by the completed release check, and verify that it does
not contain `build/`, `dist/`, `*.egg-info`, `__pycache__`, or inline snapshot audit files.

Historical records under `docs/archive/**` and private workflow configurations are
excluded even though reviewed current files under `docs/` or `configs/` may be publishable.

Do not publish Python or TypeScript artifacts built directly from the private research workspace. Build release artifacts only inside the reviewed public snapshot or the clean public repository; package discovery in the private workspace can otherwise include local research modules that the snapshot intentionally excludes.

## Credential-Free Baseline

The following command must run without WQB credentials:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_open_source_readiness tests.test_wqb_client tests.test_wqb_mcp_server
```

CI must keep this property. Tests that require live WQB access should be opt-in and skipped by default.

## Safety Boundary

This is not an official WorldQuant or WorldQuant BRAIN project. Real WQB simulation and submission can consume user quota or affect account state. Public examples must default to dry-run or audit-only behavior.

Automatic submission must be explicit, logged, and reviewable. Do not add examples that hide live submission behind a generic "run all" command.

Autonomous platform mutations use separate runtime capabilities:

```text
WQB_LIVE_SIMULATION_CAPABILITY=1
WQB_LIVE_SUBMIT_CAPABILITY=1
```

Both default to disabled. Workflow configuration expresses intent but cannot grant either capability. The low-level WQB client and MCP capability surface remain policy-neutral; enforcement belongs at autonomous loop, worker, daemon, and batch execution boundaries.

## Last Verified Baseline

On 2026-07-14, the locked local environment completed:

- full Ruff with no findings;
- 665 Python tests passed, 1 skipped, and 201 subtests passed;
- MCP and UI tests, type checks, and builds;
- runtime and full-profile bootstrap from the generated public snapshot;
- private-checkout, clean-checkout, and public-snapshot wheel installation smoke tests;
- public sdist inventory with no private or archived paths;
- Gitleaks scan of the generated public snapshot with no findings;
- Python, MCP, and UI vulnerability audits and CycloneDX SBOM generation with no known
  vulnerabilities or unresolved license decisions.

`python -m scripts.dev release-check --json` completed all 19 machine stages with
`status=pass` and no manual gates. The generated public snapshot passed installation,
secret scanning, vulnerability audits, dependency-license policy, and Python/MCP/UI SBOM
generation. The public repository and GitHub Private Vulnerability Reporting are active;
each later release must repeat this check before its clean snapshot is pushed.
