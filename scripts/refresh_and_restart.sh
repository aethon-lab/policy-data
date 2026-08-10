#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
docker compose -f deploy/compose.yml --profile refresh run --rm refresh
docker compose -f deploy/compose.yml ps --status running serve
