# Agent Onboarding

Treat this repository as a WQB research system. Do not enable live simulation or
submission capabilities during setup.

Read `docs/architecture/REPOSITORY_LAYOUT.md` before choosing an entry point. Root-level
compatibility scripts and unlisted maintenance scripts are not onboarding entry points.

## Setup protocol

1. Run `uv run python -m scripts.dev doctor --profile runtime --json` when `uv` is available.
2. If `uv` is missing on Windows, run `powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -InstallUv` only after the user accepts the official pinned installer.
3. Use `scripts/bootstrap.ps1 -Profile runtime` or `sh scripts/bootstrap.sh --profile runtime` for the Python research runtime.
4. Use the `full` profile only when MCP/UI development is required. It needs Node.js 22.12+ or 24 LTS.
5. Read `actions` and `next_command` from doctor JSON. Do not guess around a failed check.

Never write credentials outside `.env`. Never change either `WQB_LIVE_*_CAPABILITY` value
to `1` as part of onboarding, testing, or troubleshooting. Validate local setup with the
credential-free `wqb-engine demo` command first.
