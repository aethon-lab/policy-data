# Policy Data Italia

Open, source-backed parliamentary roll-call data for Italy. The first release
covers the XIX Legislature in both the Camera dei deputati and the Senato della
Repubblica, with a public website, downloads, REST API, and MCP server.

The canonical model separates official source records, normalized facts,
derived data, and interpretation. It is legislature-aware and keeps canonical
people independent of a chamber or mandate, allowing future election
candidacies and constituency records to attach without changing parliamentary
identities.

## Development

```sh
uv sync --frozen --all-groups
uv run pytest -q
```

Production packaging lives in `deploy/`. It runs as a dedicated non-root
container with a read-only filesystem and exposes the backend only through an
explicit loopback or private-proxy overlay. See `docs/runbooks/deploy-catone.md`.

The public query surfaces are release-pinned. Candidate and constituency data
can later attach to the canonical `person_id`, which is what enables a future
“current candidates in my district who voted for this measure” query without
rewriting parliamentary history.

Official-source data keeps the publisher's attribution and license. Repository
code is licensed under AGPL-3.0-or-later.
