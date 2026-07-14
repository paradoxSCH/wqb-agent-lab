# Governance

WQB Agent Lab is currently maintained by `paradoxSCH`. The maintainer owns release,
roadmap, dependency, platform-contract, and repository-policy decisions.

## Changes

Contributions are proposed through GitHub issues and pull requests. Changes to public
commands, schemas, platform side effects, memory policy, or research-policy semantics
should include tests and a concise design note. The maintainer may request revisions or
decline changes that expose private research assets, weaken auditability, or broaden the
supported platform scope without a tested contract.

## Releases

Releases follow [docs/maintainers/VERSIONING.md](docs/maintainers/VERSIONING.md). A release
must pass the repository release check from the clean public history. The tag workflow
publishes Python artifacts, CycloneDX SBOMs, and SHA-256 checksums.

## Conduct And Security

Project participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security reports
must use GitHub Private Vulnerability Reporting as described in [SECURITY.md](SECURITY.md).

If maintainership expands, this document will be updated before another person receives
release or repository-administration authority.
