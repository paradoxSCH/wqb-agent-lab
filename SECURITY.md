# Security Policy

## Supported Versions

The project is pre-1.0. Security fixes target the current `main` branch.

## Reporting

Please report credential leaks, unsafe default live submission behavior, or platform-state
bugs through GitHub Private Vulnerability Reporting. This channel is enabled on the public
repository. Do not include secrets or account-specific data in a public issue.

## Secret Handling

- Store WQB credentials only in local environment variables or `.env`.
- Never commit `.env`, logs, callback payloads, local memory databases, or real registry state.
- Do not paste WQB session cookies, tokens, alpha IDs tied to private accounts, or account email addresses into issues.

## Live Platform Safety

Commands that consume simulation budget or submit alphas must be opt-in. CI and public examples must not require WQB credentials.
