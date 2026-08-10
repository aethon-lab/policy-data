# Contributing to Policy Data Italia

Thank you for helping make Italian parliamentary data easier to inspect and
reuse. Contributions are welcome from developers, researchers, journalists,
designers, and people who know the parliamentary record well.

## Before you start

- Open an issue for a new data source, schema change, or substantial feature.
- Link claims about parliamentary data to an official Camera or Senato source.
- Keep official facts separate from derived metrics and editorial interpretation.
- Never silently “fix” an upstream value. Preserve it and document the mapping.
- Do not commit API keys, email addresses, downloaded personal data, or runtime
  databases.

Small corrections and documentation improvements can go directly to a pull
request.

## Development setup

Policy Data requires Python 3.13 and `uv`.

```sh
git clone https://github.com/aethon-lab/policy-data.git
cd policy-data
uv sync --frozen --all-groups
uv run pytest -q
```

Run the complete local check before submitting:

```sh
make check
```

For a production configuration check:

```sh
make compose-config
```

## Working with data

Tests use small synthetic or reduced official-format fixtures in
`tests/fixtures/`. Fixtures must not be presented as production data. A change to
an adapter should include:

1. a fixture that demonstrates the source shape;
2. a test for the normalized result;
3. a reconciliation or failure test for ambiguous data;
4. provenance that reaches the exact source record.

Stable identifiers and enum meanings are public contracts. Propose migrations
instead of changing their semantics in place.

## Pull requests

Keep each pull request focused. Explain:

- what changed and why;
- which official records or contracts are affected;
- whether the schema, API, MCP, or exports changed;
- which checks you ran;
- any known data-quality limitation.

Code should pass Ruff, mypy in strict mode, pytest, and the Compose configuration
check. User-facing changes should remain usable in Italian and accessible with a
keyboard.

## Data corrections

A correction report is most useful when it includes the public Policy Data URL,
the official Camera or Senato URL, the expected value, and the observed value.
Do not include sensitive personal information that is not already part of the
official public record.
