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

Official-source data keeps the publisher's attribution and license. Repository
code is licensed under AGPL-3.0-or-later.
