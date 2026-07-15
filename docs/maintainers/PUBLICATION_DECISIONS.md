# Publication Decisions

This record captures repository-owner decisions for public releases. It is current as of
2026-07-14 and is owned by the maintainer `paradoxSCH`.

## Confirmed decisions

1. **Platform scope**: the first public release remains focused on WorldQuant BRAIN. The
   maintainer has confirmed that publication of this client and workflow is permitted.
2. **Operator catalog**: redistribution of the packaged catalog is approved by the
   maintainer. It is published as factual interoperability metadata, without copied
   platform documentation prose or a claim of platform endorsement.
3. **History boundary**: the public repository must be initialized from the reviewed
   `dist/public-snapshot` filesystem snapshot. The private repository and its Git history
   must never be pushed, mirrored, squashed, or grafted into the public repository.
4. **Capability boundary**: real simulation, submission, and submission-worker
   implementations remain public. Public examples and CI keep both live capabilities
   disabled; users must grant runtime capabilities explicitly.
5. **Private research boundary**: real alpha expressions, scan recipes, submission
   records, account state, run outputs, local memory stores, and private workflow
   configurations remain private. Public examples use synthetic or non-competitive data.
6. **Identity and release**: the project name is `wqb-agent-lab`, the maintainer identity
   is `paradoxSCH`, and the first public Git tag `v0.1.0-alpha` was published from a clean
   snapshot on 2026-07-13. The current release is `v0.2.0a1`, with equivalent PEP 440
   and SemVer prerelease metadata for Python and npm packages.
7. **Support commitment**: this is a best-effort, single-user alpha release.
   The supported baseline is Python 3.11-3.12 and Node.js 22.12+ or 24 LTS. Windows and
   CI Ubuntu are the primary verified environments. No alpha quality, reward, profit, or
   long-term compatibility guarantee is made.

## Security channel verification

GitHub Private Vulnerability Reporting was enabled on the empty public repository on
2026-07-13 and the API returned `enabled=true`. The maintainer then created, read, and
closed the synthetic private advisory `GHSA-qjp9-4g3h-34jv`; it did not describe a real
vulnerability. GitHub correctly rejected an attempt by the repository administrator to
use the external-reporter endpoint against their own repository and directed the
administrator to the private advisory workflow instead.

All owner-controlled and external publication gates are closed. Each public snapshot may
report `publish_ready=true` only after its machine verification passes.
