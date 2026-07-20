# Migration Guide

## Production workflow command

The canonical production workflow command is:

```powershell
.\.venv\Scripts\python.exe -m scripts.run.workflow --workspace-root . --workflow-config .local\research\workflows\production.json --run-once --dry-run
```

`python -m scripts.kimi_daily_workflow` was removed in `0.3.0`. Use the canonical command
above.

The default workflow config moved from the historical provider-specific path to
`.local/research/workflows/production.json`. Explicit `--workflow-config` paths continue
to work.

## Python platform imports

Use `wqb_agent_lab.platform` for the installed WQB client, normalized models, readiness
checks, and operator catalog. The `src.wqb_agent_lab`, `src.wqb`, and unused `src.wq`
namespaces were removed in `0.3.0`.

## Scan imports

Use `python -m scripts.run.scan` as the command entrypoint and
`wqb_agent_lab.runtime.scan` for Python imports. The root `run_scan` module was removed in
`0.3.0`.

## Legacy scheduler

The experimental continuous scheduler was removed in `0.3.0`. Use
`scripts.run.workflow`; historical scheduler state is retained as run evidence but is not
an executable compatibility surface.

## LLM configuration

Use the top-level `llm_provider` object. The legacy `llm_adapter`, `deepseek_v4_pro`,
`kimi_cli`, and Kimi environment fallbacks remain readable through version `0.2.x` and
emit compatibility diagnostics. They will be removed in version `0.3.0`.
