# JSON contract ownership

The files in this directory are the public JSON contracts exposed by
`wqb-engine schemas.list`, `schemas.digest`, and `contracts.validate`. They are
versioned product interfaces, not examples or generated run output.

| Contract | Owner | Boundary | Runtime enforcement |
| --- | --- | --- | --- |
| `candidate` | research planning | candidate hypothesis and expression exchange | explicit validation |
| `diagnosis` | evaluation and policy feedback | structured failure diagnosis | explicit validation |
| `memory_event` | memory governance | durable memory promotion and decay events | explicit validation |
| `plan_proposal` | research planning | provider-neutral hypotheses, alternatives, and requested actions | explicit validation before execution |
| `research_policy` | research policy | budget and behavioral-boundary configuration | automatic and explicit validation |
| `run_manifest` | runtime provenance | immutable code, configuration, provider, research, and artifact identities | explicit validation at manifest writes |
| `run_summary` | runtime API and UI | read-only run status projection | explicit validation |
| `simulation_request` | platform boundary | normalized simulation input | explicit validation |
| `simulation_result` | platform boundary | normalized simulation outcome | explicit validation |
| `submission_job` | submission worker | queued submission intent and state | explicit validation |

`research_policy` is currently the only contract automatically enforced when
the production runtime loads configuration. The other contracts are published
validation boundaries: callers can validate them through the engine, but their
presence does not imply that every producer and consumer is already wired to
validate every artifact automatically.

Changes to required fields, accepted values, or field meanings follow
[the repository versioning policy](../docs/maintainers/VERSIONING.md). Additive
fields must remain compatible with existing consumers. Use schema digests to
detect exact file changes; do not treat a digest as a semantic version.
