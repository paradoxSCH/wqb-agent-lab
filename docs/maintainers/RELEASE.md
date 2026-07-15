# Release Process

## Automated verification

From a clean checkout:

```powershell
uv sync --extra dev --extra mcp --frozen
uv run python -m scripts.dev check
uv run python -m scripts.dev test
uv run python -m scripts.dev build
uv run python -m scripts.dev release-check --json
```

Release verification covers Ruff, scoped Pyright checks, a 70% Python coverage floor,
Python/MCP/UI tests and builds, non-editable wheel installation, clean-checkout build,
public-snapshot audit, vulnerability reports, license inventory, and CycloneDX SBOMs.
Generated outputs remain under ignored `dist/` paths. CodeQL runs independently for
Python and JavaScript/TypeScript on pull requests, `main`, and a weekly schedule.

The public-snapshot smoke stage builds from a temporary copy. The pristine source snapshot
remains under `dist/release-check/public-snapshot`, while provenance and hash reports remain
in the adjacent `public-snapshot-audit` directory. Commit neither the sidecar audit files nor
generated build metadata to the public repository.

Push a reviewed PEP 440 tag such as `v1.2.0`, `v1.2.0a1`, `v1.2.0b1`, or `v1.2.0rc1`
only from the clean public repository. The tag, `pyproject.toml`, `CITATION.cff`, and the
equivalent SemVer TypeScript package versions must match. The release workflow runs
the same verification and publishes the wheel, source distribution, Python/MCP/UI
CycloneDX SBOMs, `SHA256SUMS`, and GitHub build-provenance attestations. Do not publish
artifacts built in the private research workspace.

## Publication gates

The operator-catalog, history, capability, research-asset, identity, and support decisions
are recorded in [PUBLICATION_DECISIONS.md](PUBLICATION_DECISIONS.md).

GitHub Private Vulnerability Reporting is enabled and its private advisory lifecycle has
been tested. No manual publication gate remains. Other WQB open-source repositories are
useful prior art but do not replace the recorded maintainer decisions.
