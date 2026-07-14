# Dependency Trust Boundaries

## Python

`pyproject.toml` is the only hand-edited dependency declaration and `uv.lock` is the
reproducible lock. CI installs with `uv sync --extra dev --extra mcp --frozen`.

WQB credentials are handled only by the repository-owned implementations under
`wqb_agent_lab.platform`. The transport uses `requests` and targets the configured WQB
API origin; authentication, retry, pagination, and side-effect entry points have contract
tests that run without credentials or network access.

## Node

MCP and UI are separate packages with separate `package-lock.json` files. CI installs each
with `npm ci`; neither package reads WQB credentials or calls WQB HTTP endpoints directly.

## Release evidence

`python -m scripts.checks.supply_chain` creates vulnerability reports, license inventory,
and Python/MCP/UI CycloneDX SBOMs under `dist/audit/`. Unknown or disallowed Python license
metadata fails unless an exact package/version exception with rationale is recorded in
`release/allowed_dependency_licenses.json`.
