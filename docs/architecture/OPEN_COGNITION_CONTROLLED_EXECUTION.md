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
| Recoverable stages | In progress | Planning, scan preflight, and simulation use atomic checkpoints |
| Side-effect reconciliation | In progress | Simulation recovery implemented; submission recovery remains planned |
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

The stage checkpoint contract deliberately leaves `output` and `extensions` open. Only
orchestrator-owned stage identifiers and lifecycle states are enumerated. Replay-safe
stages may resume an interrupted attempt; side-effecting stages must use reconciliation
and are never replayed merely because a checkpoint remained in `running` state.

The scan-preflight checkpoint covers stage selection, budget slicing, research-policy
evaluation, diversity selection, expression preflight, and local config generation. Its
input digest includes only causally relevant state. It stops before `execute_scan`, so this
migration cannot create a remote simulation side effect.

The simulation checkpoint uses `reconcile` replay policy. Every canonical simulation
creation now passes through the operation journal before its POST. If the process loses a
response or stops with a `started` operation, the next run does not repeat that request.
It first polls a durable simulation location when available, then looks for a recent Alpha
matching the complete requested settings and expression. Only positive remote evidence can
resolve the operation as accepted. Missing evidence schedules another read-only observation;
after the bounded observation window, the operation moves to explicit manual review and
continues to block new simulation POSTs. Recovered results are atomically materialized into
the normal scan result file before the stage may resume.

This gate fingerprints the complete candidate payload and does not enumerate operators,
mechanisms, fields, or expression shapes. Novel LLM output therefore follows the same
recovery protocol without being removed or narrowed.

### 5. Side-effect reconciliation

- Simulation outcomes: implemented with operation fingerprints, location polling, recent
  Alpha evidence, retry scheduling, and explicit manual review.
- Submission outcomes: extend the same evidence-first protocol at the submission worker.
- Keep fault-injection coverage for response loss, hard process interruption, ambiguous
  matches, and mismatched settings.

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
