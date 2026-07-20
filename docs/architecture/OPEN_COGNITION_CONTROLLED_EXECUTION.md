# Open Cognition / Controlled Execution Implementation Plan

This plan incrementally replaces the monolithic production workflow without changing
live-capability defaults or narrowing the research space available to an LLM.

## Invariants

The migration must preserve all of the following:

- A model may propose an unknown mechanism, arbitrary expression, new proxy field, new
  action kind, alternatives, and a reasoned request for a soft-policy exception.
- Invalid structure is returned to the model for bounded repair before a proposal is
  rejected.
- A model cannot enable live capabilities, exceed a deterministic budget, resolve an
  ambiguous remote side effect, or submit an alpha by emitting text.
- Rejected and deferred actions retain the original proposal, evidence, and explanation.
- Tests and onboarding remain credential-free and never enable live simulation or
  submission.

## Implementation status

| Slice | Status | Current boundary |
| --- | --- | --- |
| Proposal boundary | Implemented | Provider-neutral schema and immutable models |
| Structural repair and policy | Implemented | Explicit opt-in adapter; legacy output remains default |
| Provenance | In progress | Production tick checkpoints, configuration/schema/artifact digests |
| Recoverable stages | Planned | Extract one stage at a time after golden-run parity |
| Side-effect reconciliation | Planned | Extend the existing operation journal with unknown-outcome recovery |
| Evidence-gated feedback | Planned | Shadow mode before advisory or control use |

## Delivery sequence

### 1. Proposal boundary

- Publish the provider-neutral `plan_proposal` schema.
- Add immutable Python proposal models and round-trip tests.
- Preserve `extensions`, alternatives, and free-form notes.

### 2. Repair and policy decisions

- Add a bounded structural repair loop to the LLM planning adapter.
- Split deterministic hard controls from advisory research guidance.
- Route unknown mechanisms and fields to an exploration lane instead of deleting them.

### 3. Provenance

- Add an immutable run manifest containing code, dependency, configuration, schema,
  provider, prompt, catalog, and artifact digests.
- Validate public artifacts at producer and consumer boundaries.

### 4. Recoverable workflow

- Extract planning, preflight, simulation, diagnosis, triage, submission, registry, memory,
  and evaluation stages behind stable interfaces.
- Make the current workflow delegate to one extracted stage at a time.
- Introduce explicit checkpoints only after golden-run parity is demonstrated.

### 5. Side-effect reconciliation

- Add a reconciler for unknown simulation and submission outcomes.
- Add retry scheduling, dead-letter state, and explicit manual-review outcomes.
- Prove crash recovery and idempotency with fault-injection tests.

### 6. Evidence-gated feedback

- Run memory retrieval and evaluation-based allocation in shadow mode.
- Compare candidate diversity, duplicate rate, and submit-ready candidates per simulation
  budget before promoting feedback to advisory or control modes.

### 7. Compatibility removal

- Remove the scheduled 0.3 compatibility surfaces only after the new production workflow
  and installed-package smoke tests are stable.

## Completion gates

- Budget never becomes negative under generated event sequences.
- Replaying a completed stage cannot create a second effective side effect.
- Every persisted public artifact validates against its declared schema version.
- A representative offline prompt set retains arbitrary expressions and unknown mechanisms.
- Structural repair improves parse success without reducing proposal diversity.
- Shadow feedback demonstrates measured benefit before controlling later runs.
