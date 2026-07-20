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
| Provenance | Implemented | Git/dependency/config/provider/prompt/catalog/schema/artifact identities |
| Recoverable stages | Implemented | Planning through registry, memory, and evaluation use atomic checkpoints |
| Side-effect reconciliation | Implemented | Simulation and submission use evidence-first recovery |
| Evidence-gated feedback | Implemented | Shadow default; control requires measured promotion gates |

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

The production manifest resolves the Git revision outside CI as well as inside it and records
whether tracked files were dirty. It fingerprints the dependency lock, workflow configuration,
provider configuration, exact persisted prompt, operator catalog, every published schema, and
every durable run artifact. Artifacts explicitly declared against a public schema are validated
before they enter the manifest; manifest consumers revalidate the schema identity and can verify
the current artifact bytes and contract. Internal diagnostic and open LLM artifacts remain
content-addressed without being forced into a closed public schema.

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

Diagnosis and triage are separate replay-safe stages. Diagnosis preserves the complete
simulation row and adds structured failure evidence. Triage consumes that evidence plus
submitted-registry state, then emits advisory routes and closed-loop artifacts. Unknown
mechanisms, settings, extension objects, and expression forms remain in both stage outputs;
route names are observations and recommendations, not an execution allowlist. Golden tests
compare the staged artifacts with the previous closed-loop implementation.

The submission stage checkpoints the durable queue only; it cannot enable live capability
or issue a remote write. A separately governed worker performs live checks and submission.
Every canonical submission POST is first recorded in the operation journal. If a response
is lost or the worker stops after the POST, the next worker tick looks up the exact Alpha ID
and accepts only `ACTIVE`, `SUBMITTED`, or `dateSubmitted` detail as positive evidence. A
missing match schedules another read-only observation and eventually moves to manual review;
it never causes an ambiguous POST to be repeated.

Registry refresh, memory ingestion, and output evaluation now have explicit stage boundaries.
Registry refresh remains a non-blocking, lock-guarded, read-only cache update; the checkpoint
records the exact local snapshot used by the current tick. Memory ingestion runs locally with
idempotent SQLite upserts and unique events, and must complete before evaluation starts. This
removes the previous race where evaluation could nondeterministically miss the current run's
memory report. Evaluation timestamps are injected by the workflow clock, and budget-policy
actions remain recorded observations rather than automatic restrictions on later LLM output.

### 5. Side-effect reconciliation

- Simulation outcomes: implemented with operation fingerprints, location polling, recent
  Alpha evidence, retry scheduling, and explicit manual review.
- Submission outcomes: implemented with exact Alpha detail evidence, bounded read-only
  observation, and manual review instead of blind POST replay.
- Keep fault-injection coverage for response loss, hard process interruption, ambiguous
  matches, and mismatched settings.

### 6. Evidence-gated feedback

- Policy feedback supports `off`, `shadow`, `advisory`, and `control` modes and defaults to
  `shadow`. Shadow mode runs the unmodified baseline candidate set while persisting the full
  counterfactual recommendation, including arbitrary candidate fields and overflow ideas.
- Each completed scan scores baseline and recommended subsets on submit-ready rate, low-value
  rate, simulation count, and distinct-family retention. Evidence is aggregated across runs.
- A request for `control` falls back to shadow unless the configured multi-run sample size,
  submit-ready non-regression, low-value improvement, and family-diversity retention gates all
  pass. Configuration may make those gates stricter but cannot weaken their conservative floors.
  Even promoted control retains an explicit exploration share and keeps overflow payloads for
  audit and later reuse. Shadow observations are not mislabeled as policy actions used.

### 7. Compatibility removal

- Remove the scheduled 0.3 compatibility surfaces only after the new production workflow
  and installed-package smoke tests are stable.

## Completion gates

- Budget never becomes negative under generated event sequences.
- Replaying a completed stage cannot create a second effective side effect.
- Every persisted artifact declared against a public contract validates at producer and
  consumer boundaries; open internal artifacts remain losslessly content-addressed.
- A representative offline prompt set retains arbitrary expressions and unknown mechanisms.
- Structural repair improves parse success without reducing proposal diversity.
- Shadow feedback must demonstrate measured benefit before controlling later runs; the runtime
  enforces this gate and otherwise continues the baseline selection unchanged.
