# ADR 0001: Local-First Layered Runtime

**Status:** Accepted

**Date:** 2026-07-13

## Decision

WQB Agent Lab remains a single-user local application. Python is the canonical runtime for
research and workflow behavior; TypeScript packages remain protocol and presentation
boundaries. Platform transports expose capabilities without embedding research or
side-effect policy. Product packages may not depend on root scripts, and non-platform
modules may not import the third-party `wqb` SDK directly.

## Consequences

- Users can run and inspect the complete system locally without hosted infrastructure.
- Quantitative and data-processing code keeps the Python ecosystem it already uses.
- MCP and UI packages can evolve independently without duplicating research logic.
- WQB transport or third-party SDK replacement does not change workflow contracts.
- Policy decisions remain testable separately from network capability code.
