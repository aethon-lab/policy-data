# Incident response

## First actions

1. Preserve logs, the active release ID, image digest, and manifest checksums.
2. If integrity or provenance is in doubt, roll back only the data pointer.
3. If an application image is suspect, recreate `serve` from the previous digest.
4. If an auth pepper or API-key material may be exposed, rotate the pepper and
   invalidate every session, OTP challenge, and API key.
5. If the Resend key is exposed, revoke it at Resend and mount a replacement.

Do not delete the failed release or control state before an encrypted evidence
copy exists. Avoid logging bearer keys, OTP values, cookies, email addresses, or
secret-file contents.

## Recovery checks

Require green health, canonical HTTPS, manifest checksums, public downloads,
missing/invalid bearer rejection, authenticated REST and MCP, dashboard login,
and one result from each chamber. Record what changed and whether the incident
affected source fidelity, service availability, or credentials.
