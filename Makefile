.PHONY: check test compose-config image

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run pytest -q

test:
	uv run pytest -q

compose-config:
	docker compose --env-file deploy/.env.example -f deploy/compose.yml -f deploy/compose.loopback.yml config --quiet

image:
	docker compose --env-file deploy/.env.example -f deploy/compose.yml build serve
