# Versioning and Deprecation

WQB Agent Lab uses semantic versioning for public commands, `wqb-engine` operations, MCP
contracts, configuration schemas, and documented Python imports.

The current public release is `v0.1.1-alpha`; package metadata is `0.1.1` and remains in
alpha development status. `v0.1.0-alpha` was the first history-free public release.

- Patch releases fix behavior without changing public contracts.
- Minor releases may add compatible operations, fields, providers, or diagnostics.
- Breaking changes require a major release after `1.0.0`; during `0.x`, they require an
  explicit migration guide and announced removal version.
- Documented public compatibility shims remain for one release cycle and delegate to one
  canonical implementation.
- Internal duplicate implementations and unreferenced scripts may be removed immediately.

The current provider-specific workflow launcher, `src.wqb` imports, and legacy LLM config
keys are scheduled for removal in `0.3.0`. Current documentation shows only their
replacements except in the migration guide.
