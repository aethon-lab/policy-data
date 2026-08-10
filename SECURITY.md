# Security policy

## Supported version

Security fixes are applied to the current `main` branch. This project is in an
early public-release phase and does not yet maintain parallel release lines.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or exposed
credential. Use GitHub's **Report a vulnerability** feature in the repository's
Security tab. Include affected endpoints or commits, reproduction steps, impact,
and any suggested mitigation.

If private reporting is unavailable, contact the repository owner privately and
share only enough detail to establish a secure follow-up channel.

We aim to acknowledge a report within seven days. Please allow time for a fix and
deployment before public disclosure.

## Scope notes

Useful reports include authentication or authorization bypasses, SSRF or unsafe
archive handling in source acquisition, injection, secret exposure, cursor or
release-integrity failures, and denial-of-service paths that bypass documented
limits.

Discrepancies in parliamentary records are data-correction issues unless they
also create a security impact. Report data corrections through a normal issue
with links to the official record.
