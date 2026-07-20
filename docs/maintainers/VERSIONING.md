# Versioning and Deprecation

WQB Agent Lab uses semantic versioning for public commands, `wqb-engine` operations, MCP
contracts, configuration schemas, and documented Python imports.

The current development version is `v0.3.0a1`; Python metadata uses PEP 440 `0.3.0a1` and
npm metadata uses equivalent SemVer `0.3.0-alpha.1`. The project remains in alpha
development status. `v0.1.0-alpha` was the first history-free public release.

- Patch releases fix behavior without changing public contracts.
- Minor releases may add compatible operations, fields, providers, or diagnostics.
- Breaking changes require a major release after `1.0.0`; during `0.x`, they require an
  explicit migration guide and announced removal version.
- Documented public compatibility shims remain for one release cycle and delegate to one
  canonical implementation.
- Internal duplicate implementations and unreferenced scripts may be removed immediately.

The legacy scheduler, provider-specific workflow launcher, `run_scan`, duplicate
`src.wqb_agent_lab` / `src.wqb` namespaces, and legacy LLM config keys were removed for
`0.3.0`. Historical names may appear only in migration and release notes.
