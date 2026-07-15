# Migration Guide

## Production workflow command

The canonical production workflow command is:

```powershell
.\.venv\Scripts\python.exe -m scripts.run.workflow --workspace-root . --workflow-config .local\research\workflows\production.json --run-once --dry-run
```

`python -m scripts.kimi_daily_workflow` remains a forwarding launcher for one release
cycle. It prints a deprecation message to stderr and will be removed in version `0.3.0`.
It does not contain a second workflow implementation.

The default workflow config moved from the historical provider-specific path to
`.local/research/workflows/production.json`. Explicit `--workflow-config` paths continue
to work.

## Python platform imports

Use `wqb_agent_lab.platform` for the installed WQB client, normalized models, readiness
checks, and operator catalog. Imports from `src.wqb_agent_lab` and `src.wqb` remain
forwarding compatibility imports through version `0.2.x` and will be removed in version
`0.3.0`.

## LLM configuration

Use the top-level `llm_provider` object. The legacy `llm_adapter`, `deepseek_v4_pro`,
`kimi_cli`, and Kimi environment fallbacks remain readable through version `0.2.x` and
emit compatibility diagnostics. They will be removed in version `0.3.0`.
