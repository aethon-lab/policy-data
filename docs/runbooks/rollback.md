# Rollback

Code and data rollback are independent.

For code, set `POLICY_DATA_IMAGE` to the previous immutable image tag or digest,
recreate `serve`, and run the remote smoke script.

For data, first validate the target directory and its manifest. Then atomically
replace `published/active.json` with the prior `release-*` identity using
`policy_data.ingest.publish.activate_release`, restart `serve`, and verify health,
one Camera result, one Senato result, downloads, REST, and MCP. Never edit an
immutable release in place.

Keep the failed release for diagnosis unless it contains data that must legally
be removed. Data rollback leaves API keys and sessions valid. A control database
restore does not; follow the credential-reset procedure in the backup runbook.
