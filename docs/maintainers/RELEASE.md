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

Release verification covers full Ruff, Python/MCP/UI tests and builds, non-editable wheel
installation, clean-checkout build, public-snapshot audit, vulnerability reports, license
inventory, and CycloneDX SBOMs. Generated outputs remain under ignored `dist/` paths.

## Publication gates

The operator-catalog, history, capability, research-asset, identity, and support decisions
are recorded in [PUBLICATION_DECISIONS.md](PUBLICATION_DECISIONS.md).

GitHub Private Vulnerability Reporting is enabled and its private advisory lifecycle has
been tested. No manual publication gate remains. Other WQB open-source repositories are
useful prior art but do not replace the recorded maintainer decisions.
