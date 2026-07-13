# Dependency Trust Boundaries

## Python

`pyproject.toml` is the only hand-edited dependency declaration and `uv.lock` is the
reproducible lock. CI installs with `uv sync --extra dev --extra mcp --frozen`.

The third-party `wqb==0.2.5` package can access WQB credentials. Its direct import is
isolated in `src.wqb_agent_lab.platform.third_party`; product and operational modules use
the repository-owned platform contracts. Before upgrading it, review source provenance,
authentication behavior, network destinations, license metadata, and adapter contract
tests.

## Node

MCP and UI are separate packages with separate `package-lock.json` files. CI installs each
with `npm ci`; neither package reads WQB credentials or calls WQB HTTP endpoints directly.

## Release evidence

`python -m scripts.checks.supply_chain` creates vulnerability reports, license inventory,
and Python/MCP/UI CycloneDX SBOMs under `dist/audit/`. Unknown or disallowed Python license
metadata fails unless an exact package/version exception with rationale is recorded in
`release/allowed_dependency_licenses.json`.
