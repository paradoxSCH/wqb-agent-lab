# Changelog

## [0.2.0a1] - 2026-07-14

- Added durable side-effect operation journal and replayable workflow outbox.
- Added Chinese memory retrieval benchmark and n-gram recall.
- Added read-only WQB API contract canary and fake-server drift tests.
- Moved the canonical platform implementation to `wqb_agent_lab.platform`.
- Replaced the embedded Python dashboard with the React research workbench.
- Adopted equivalent PEP 440 and SemVer prerelease metadata.

All notable changes to WQB Agent Lab are documented in this file.

The project follows Semantic Versioning after the first public release. While the project is pre-1.0, minor releases may contain breaking contract changes and will call them out here.

## [Unreleased]

### Added

- Open-cognition/controlled-execution architecture decision and incremental migration plan.
- Provider-neutral plan proposal contract with extension fields, bounded structural repair,
  and explicit hard-versus-soft planning policy decisions.
- Immutable run provenance manifests with content-addressed artifact records and sensitive
  metadata rejection.
- Explicit opt-in `plan_proposal` output for the production LLM adapter; legacy planner
  output remains the default and structural repair does not consume WQB budget.
- Credential-safe, content-addressed `run_manifest.json` checkpoints for production daily
  workflow ticks, including failure provenance without replacing the original exception.
- Atomic workflow stage checkpoints with replay-safe interruption recovery, mandatory
  reconciliation for interrupted side-effect stages, and initial LLM-planning delegation.
- Replay-safe scan-planning and preflight checkpoints with stable causal input digests,
  golden-path parity, and an explicit boundary before remote simulation execution.

## [0.1.1-alpha] - 2026-07-14

### Added

- Repository-owned WQB session transport with authentication, pagination, simulation,
  check, and submission compatibility contracts.
- Release artifacts with checksums and CycloneDX software bills of materials.
- Protected public `main` branch and docs-as-code repository settings.

### Changed

- Replaced the third-party `wqb` runtime package with the repository-owned platform
  client and session boundary.
- Condensed the Chinese-first README and replaced the architecture image with the
  implemented runtime path only.
- Synchronized package metadata, release evidence, and publication records with the
  public repository.
- Removed positioning language that described ordinary execution defaults as product
  differentiators.

## [0.1.0-alpha] - 2026-07-13

### Added

- History-free public snapshot exporter with manifest-driven private asset exclusion.
- Python engine, TypeScript MCP shell, and React run monitor foundations.
- Structured submission governance and independent submission worker.
- Shared runtime capabilities for autonomous WQB simulation and submission.
- Release audit, complete credential-free test suite, package build checks, and supply-chain scanning.
- Canonical `scripts.dev` checks, tests, builds, and release verification with JSON reports.
- Python/OS CI matrix for Python 3.11/3.12 on Ubuntu and Windows.
- Non-editable wheel, clean-checkout, and generated public-snapshot installation smoke tests.
- Python, MCP, and UI CycloneDX SBOMs plus explicit dependency-license policy.
- Runtime/full bootstrap profiles and machine-readable onboarding diagnostics for humans and agents.
- Repository-owner publication decisions covering operator metadata, clean history, live capabilities, private research assets, identity, and support scope.

### Changed

- Replaced MIT with Apache-2.0 for software and CC BY 4.0 for documentation and visual assets; added NOTICE and machine-readable citation metadata.
- Aligned agent-facing operator and direction knowledge with the current catalog and production workflow configuration.
- Documented the open memory, evaluation, and automatic-submission feedback links and replaced the stale self-evolution architecture image.
- Made `pyproject.toml` plus `uv.lock` the only committed Python dependency source and removed the unused OpenAI SDK dependency.
- Moved the canonical WQB boundary to `wqb_agent_lab.platform`; `src.wqb` and
  `src.wqb_agent_lab.platform` remain one-cycle compatibility imports.
- Standardized the production launcher as `python -m scripts.run.workflow`; the provider-specific launcher is deprecated until `0.3.0`.
- Split current documentation into user, architecture, and maintainer sections; historical implementation records are archived and excluded from releases.
- Removed the operator-catalog manual gate after maintainer confirmation; GitHub Private Vulnerability Reporting remains the only external publication gate.

### Security

- Public examples disable live simulation and submission by default.
- Private run data, research recipes, credentials, local settings, and Git history are excluded from public snapshots.
- Gitleaks scans the generated history-free public snapshot, while dependency audits, license checks, and SBOM reports remain release evidence.
- GitHub Private Vulnerability Reporting is enabled and its private advisory lifecycle has been tested.
