# Documentation

WQB Agent Lab documentation is divided by authority and audience.

## User documentation

- [首次安装](user/GETTING_STARTED.md)：runtime/full 两档安装、版本基线与首次验证。
- [安装诊断](user/TROUBLESHOOTING.md)：doctor 输出、错误码和 Agent 修复规则。
- [Research policy](user/RESEARCH_POLICY.md): budgets and behavioral boundaries.
- [LLM providers](user/LLM_PROVIDERS.md): provider-neutral model configuration.
- [Migration guide](user/MIGRATING.md): bounded compatibility paths and removal versions.

## Architecture

- [Current architecture](architecture/README.md): runtime ownership and dependency direction.
- [仓库目录](architecture/REPOSITORY_LAYOUT.md)：公开目录、稳定入口与兼容层边界。
- [Architecture decisions](architecture/decisions/0001-layered-python-typescript-runtime.md): accepted design constraints.

## Maintainers

- [Release process](maintainers/RELEASE.md): reproducible release verification.
- [Open-source readiness](maintainers/OPEN_SOURCE_READINESS.md): public/private boundaries.
- [Publication decisions](maintainers/PUBLICATION_DECISIONS.md): owner-approved release boundaries and remaining gate.
- [Dependency trust](maintainers/DEPENDENCY_TRUST.md): credential and supply-chain boundaries.
- [Versioning](maintainers/VERSIONING.md): compatibility and deprecation policy.

`archive/` contains historical implementation records. It is not current operational
documentation, is not indexed by the research agent, and is excluded from public release
artifacts. Git history is used instead of retaining low-value or factually wrong documents.
