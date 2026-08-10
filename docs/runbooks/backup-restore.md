# Backup and restore

Parliamentary releases are immutable and can be copied independently. The
control database contains account, session, and API-key state and requires a
consistent SQLite backup that includes WAL activity.

## Backup

1. Create an encrypted backup destination with access restricted to the operator.
2. Use SQLite's online backup API from the serving container to copy
   `/var/lib/policy-data/control.sqlite3` to a temporary file on a dedicated
   backup mount.
3. Encrypt the copy before it leaves Catone and record its checksum and timestamp.
4. Copy `published/active.json`, the active release directory, and at least one
   previous release. Verify their manifest checksums after copying.

Never copy only the live database file while ignoring its WAL. Never store the
pepper or Resend key in the same backup archive.

## Restore

Stop `serve`, restore the control database into the named control volume, check
ownership is UID/GID 10001, and run `PRAGMA integrity_check` before restart.
A control-state restore is a credential reset event: rotate `AUTH_PEPPER`, revoke
all previous sessions, challenges, and API keys, and notify affected operators.

The privacy-retention purge runs whenever the application starts and can also be
run explicitly with `python -m policy_data.access_maintenance purge`. For a v1
manual deletion request, resolve the requester to the dashboard's internal
account ID and run `python -m policy_data.access_maintenance delete-account
acct_...` from the serve container. This permanently removes the account,
sessions, challenges linked after verification, and API keys; do not accept an
email address on the command line.
Restoring a data release alone does not alter credentials.
