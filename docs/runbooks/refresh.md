# Release refresh

The serving process never writes releases. A separate, network-isolated
`refresh` container can validate an already built source-backed release and is
the only container with write access to `published`.

```sh
docker compose --env-file .env -f deploy/compose.yml --profile refresh run --rm refresh
```

The command verifies the active manifest and every declared checksum. Readers
resolve the active pointer per query, so data activation does not require a
restart. The job has no control database, API-key pepper, cursor secret, Resend
credential, or proxy network access.

The official-source acquisition-to-release orchestration command is deliberately
not represented as complete yet. Until it lands, build candidate releases with
the tested adapters and `ReleaseBuilder`, transfer them into `published/releases`,
validate them, and atomically activate them. Do not install the timer under the
name “source refresh” until that orchestration is connected.

When connected, install the supplied unit and timer under `/etc/systemd/system`,
then run `systemctl daemon-reload` and `systemctl enable --now
policy-data-refresh.timer`. A failed one-shot remains visible in systemd and does
not create a restart loop.
