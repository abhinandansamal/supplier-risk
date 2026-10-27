#!/usr/bin/env bash
# Synthetic ERP helper.
#   scripts/erp_up.sh generate   write schema, seed and CSVs (seed 42, today) and copy the SQL
#                                into the erp-db init folder
#   scripts/erp_up.sh up         generate, start erp-db and wait until it is healthy
#   scripts/erp_up.sh reset      delete the erp-db volume (asks first), then up
# Extra arguments after the command go to the generator, e.g. --today 2026-10-27.
set -euo pipefail

cd "$(dirname "$0")/.."
ERP_DIR=data_pipeline/synthetic_erp

generate() {
  uv run python -m data_pipeline.synthetic_erp.generate "$@"
  cp "$ERP_DIR/out/schema.sql" "$ERP_DIR/init/01_schema.sql"
  cp "$ERP_DIR/out/seed.sql" "$ERP_DIR/init/02_seed.sql"
}

up() {
  generate "$@"
  docker compose up -d --wait erp-db
  echo "erp-db is healthy on 127.0.0.1:5433"
}

reset() {
  read -r -p "Delete the erp-db volume and all its data? [y/N] " answer
  [[ "$answer" == "y" ]] || { echo "Aborted."; exit 1; }
  docker compose down -v
  up "$@"
}

usage() {
  echo "usage: $0 {generate|up|reset} [generator args]"
  echo "  generate  write schema, seed and CSVs and copy the SQL into the erp-db init folder"
  echo "  up        generate, start erp-db and wait until it is healthy"
  echo "  reset     delete the erp-db volume (asks first), then up"
}

command="${1:-}"
shift || true
case "$command" in
  -h|--help) usage; exit 0 ;;
  generate) generate "$@" ;;
  up) up "$@" ;;
  reset) reset "$@" ;;
  *) usage >&2; exit 2 ;;
esac
