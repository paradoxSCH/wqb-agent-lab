# Current Architecture

WQB Agent Lab is a research system for WorldQuant BRAIN. Python owns quantitative research,
workflow state, policy, memory, evaluation, and the WQB platform boundary. TypeScript owns
the MCP protocol shell and monitoring UI.

![Current WQB Agent Lab architecture](../assets/wqb-agent-architecture-current-zh.svg)

## Dependency direction

```text
UI / MCP / commands
        |
        v
workflow -> research / memory / evaluation / governance / llm
        |
        v
wqb_agent_lab.platform
        |
        v
WorldQuant BRAIN transport
```

- `src.wqb_agent_lab.platform` is the canonical WQB client, model, readiness, and operator
  catalog boundary.
- `src.wqb_agent_lab.workflow.ResearchWorkflow` is the public production orchestrator.
- `src.wqb` and `scripts.kimi_daily_workflow` are one-cycle compatibility surfaces only.
- Transport and MCP tools expose facts and capabilities. Governance decides budgets,
  retries, pauses, promotion, and side effects.
- Mutable runs, memory, credentials, scans, registries, logs, and PID files live under
  ignored local-state paths.

## Verification

The architecture boundaries are executable in `tests/test_architecture_boundaries.py` and
`tests/test_platform_boundary.py`. Developer and CI verification share `python -m scripts.dev`.
