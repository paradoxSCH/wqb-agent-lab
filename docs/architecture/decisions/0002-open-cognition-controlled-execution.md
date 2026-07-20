# ADR 0002: Open Cognition and Controlled Execution

**Status:** Accepted

**Date:** 2026-07-20

## Context

The research runtime needs two properties that are easy to conflate:

1. an LLM must be able to propose novel hypotheses, mechanisms, expressions, evidence,
   alternatives, and requests for policy exceptions; and
2. budget-consuming or irreversible actions must remain deterministic, recoverable, and
   auditable.

Encoding research taste as a broad set of hard allowlists would make the planner behave
like a rule engine. Allowing model output to call simulation or submission transports
directly would make budget and platform state impossible to reconcile reliably.

## Decision

WQB Agent Lab separates cognition from execution.

- LLM providers produce an open, provider-neutral plan proposal. The proposal preserves
  free-form notes and extension objects so new research ideas do not require a product
  release before they can be represented.
- Structural contract failures enter a bounded repair loop. They do not consume WQB
  simulation budget and are not treated as research-policy failures.
- Hard controls are limited to credentials, explicit live capabilities, budget ceilings,
  idempotency, unresolved remote side effects, and other irreversible-action safety
  invariants.
- Research preferences are soft controls by default. They may rank, annotate, route to an
  exploration lane, or request review, but do not erase the original proposal.
- The deterministic workflow state machine owns stage transitions and invokes platform
  capabilities. LLM output requests actions; it never grants itself a capability.
- Memory and evaluation feedback enter production in shadow mode before they may influence
  budget allocation or execution.

## Consequences

- The planner can evolve independently from workflow persistence and platform transports.
- Supporting a new model or provider does not require that it implement a vendor-specific
  structured-output or tool-calling API.
- Every rejected action still leaves an auditable research proposal and policy decision.
- The system needs explicit proposal, policy-decision, stage-result, and run-manifest
  contracts plus compatibility tests.
- More deterministic orchestration code is required, but the product remains a modular
  single-user application rather than becoming a distributed system.

## Non-goals

- Enumerating every allowed expression, operator, behavioral mechanism, or action kind in
  the LLM contract.
- Giving the model direct access to live simulation or submission transports.
- Automatically enabling either `WQB_LIVE_*_CAPABILITY` value.
- Introducing microservices, a remote queue, or a multi-user database for the current
  single-user product.
