# Deploy on Catone

The application runs in its own Compose project, `policy-data`. The backend is
never published on all interfaces. Choose exactly one ingress overlay:

- `compose.loopback.yml` for a host reverse proxy reaching `127.0.0.1:8019`.
- `compose.proxy.yml` for a reverse proxy attached to an external Docker network.

## Host preparation

1. Install Docker Engine with the Compose plugin and create `/opt/policy-data`.
2. Place the repository checkout there, owned by the unprivileged deployment user.
3. Copy `deploy/.env.example` to `.env` and replace every example value.
4. Create `deploy/secrets/{cursor_secret,auth_pepper,resend_api_key}` with mode
   `0600`. The first two values must be independent random values of at least 32
   bytes. Never commit them.
5. Create `deploy/state/{raw,staging,published}` and make them writable by UID
   10001. Only `published` is mounted into the serving container, read-only.

## Start through loopback

```sh
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.loopback.yml config --quiet
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.loopback.yml build serve
docker compose --env-file .env -f deploy/compose.yml --profile refresh run --rm refresh
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.loopback.yml up -d serve
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.loopback.yml ps
```

Configure the existing reverse proxy to preserve `Host`, `Authorization`, and
`Mcp-*` headers and proxy to `http://127.0.0.1:8019`. TLS terminates at the
existing proxy. Strip client-supplied forwarding headers, set `X-Forwarded-For`
from the trusted connection, and apply connection/bandwidth limits to `/releases/`.
`TRUSTED_PROXY_IPS=*` is safe only while the container remains reachable solely
through this loopback/private proxy boundary; otherwise replace it with the exact
proxy addresses before starting the service.

Run `scripts/smoke_deployed.py --base-url "$PUBLIC_SITE_URL"` after DNS and TLS
are live. Supply `POLICY_DATA_API_KEY` only through the process environment when
testing authenticated REST.

## Updating code

Build the new immutable image first, run the local checks, then recreate only
`serve`. Keep the previous image tag until remote smoke passes. A restart without
publisher access continues to serve the active immutable release.
