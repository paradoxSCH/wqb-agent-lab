# Contributing

Thanks for helping make WQB Agent Lab more useful and easier to trust.

## Development Setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Profile full
uv run python -m scripts.dev doctor --profile full --json
uv run python -m scripts.dev check
uv run python -m scripts.dev test
```

The supported development baseline is Python 3.11-3.12 and Node.js 22.12+ or 24 LTS.
Do not replace the committed `uv.lock` or package lockfiles during setup.

## Contribution Rules

- Keep tests credential-free unless the test is explicitly marked as live and skipped by default.
- Do not commit `.env`, logs, PID files, local memory databases, callback outboxes, or real run outputs.
- Route new WorldQuant BRAIN platform interactions through `wqb_agent_lab.platform`.
- Keep automatic submission explicit, logged, and disabled in public examples.
- Prefer small focused PRs with a spec or design note for behavior changes.

## Pull Request Checklist

- Unit tests pass without WQB credentials.
- New WQB API behavior has fake-session tests.
- Documentation is updated when public commands, config, or safety boundaries change.
- No private alpha results, credentials, or account-specific registry state are included.
