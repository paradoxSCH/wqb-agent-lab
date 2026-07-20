# Changelog

## [0.3.0a1] - 2026-07-21

### Removed

- Removed the experimental continuous scheduler, its separate LLM template generator,
  compatibility command, and implementation-coupled test suite. `ResearchWorkflow` is now
  the only production orchestrator.
- Removed the expired root scan/workflow launchers and the `src.wqb`, `src.wqb_agent_lab`,
  and unused `src.wq` compatibility namespaces.
- Removed legacy LLM configuration fallbacks. `llm_provider` is now the only runtime LLM
  configuration surface, including for CLI-backed models.
- Moved contracts, memory, evaluation, governance, candidate-generation, configuration,
  locking, and atomic-write implementations into the canonical `wqb_agent_lab` package.
- Moved the provider-neutral `ResearchWorkflow`, LLM provider stack, research policy,
  memory sync, decision attribution, and feedback governance into `wqb_agent_lab`; the
  canonical package no longer imports `src`.
- Split workflow artifact/provenance I/O, candidate budget selection, and stage planning
  data out of the orchestration engine.
- Split workflow reporting, scan-configuration rotation, and CLI parsing into explicit
  services with typed orchestration boundaries.
- Isolated submitted-alpha registry snapshots and replay-safe memory/evaluation
  postprocessing behind typed workflow service boundaries.
- Moved deterministic diagnosis, triage routing, deduplication, and family-efficiency
  calculations into stateless workflow-domain functions while preserving open fields.
- Moved alpha generation/refinement, behavioral-proxy analysis, self-correlation repair,
  scoring, loop validation, and policy-effectiveness analysis out of `src` and into the
  canonical research/evaluation packages.
- Moved the research-session adapter, simulation/submission helpers, and result cache out
  of `src` into the canonical platform/runtime packages.

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
- Reconciliation-gated simulation checkpoints and operation-journal recovery for
  interrupted or ambiguous simulation creation, with positive-evidence matching,
  read-only retry scheduling, and explicit manual-review outcomes instead of blind POST
  replay.
- Replay-safe diagnosis and triage stages with causal input digests, deterministic
  timestamps, golden-path parity, and preservation of novel candidate fields through
  advisory routing.
- Durable submission-intent checkpoints and evidence-first recovery for lost responses or
  hard worker interruptions, preventing ambiguous submission POST replay.
- Checkpointed registry, memory, and evaluation stages with deterministic evaluation time
  and strict memory-before-evaluation ordering, removing an asynchronous artifact race.
- Counterfactual policy-feedback shadow mode by default, multi-run promotion gates for control,
  explicit exploration retention, and full preservation of overflow LLM candidate payloads.
- Complete production provenance for local/CI Git state, prompts, provider configuration,
  operator catalogs, schemas, and schema-declared artifacts with producer/consumer validation.

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
