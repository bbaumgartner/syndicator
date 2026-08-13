#!/usr/bin/env bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="${SYNDICATOR_SOURCE_ROOT:-$ROOT}"
ENV_FILE="${SYNDICATOR_ENV_FILE:-$ROOT/.env}"

cd "$ROOT" || exit 1

resolve_from_root() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s\n' "$ROOT/${path#./}"
  fi
}

compose() {
  local env_file="${SYNDICATOR_ENV_FILE:-$ENV_FILE}"
  local args=(
    --project-directory "$SOURCE_ROOT"
    -f "$SOURCE_ROOT/docker-compose.yml"
  )
  if [[ -f "$env_file" ]]; then
    args+=(--env-file "$env_file")
  fi
  if [[ -n "${SYNDICATOR_PROJECT:-}" ]]; then
    args+=(-p "$SYNDICATOR_PROJECT")
  fi
  docker compose "${args[@]}" "$@"
}

wait_for_n8n() {
  local attempts="${1:-60}"
  local delay="${2:-2}"
  local count
  echo "Waiting for n8n..."
  for ((count = 1; count <= attempts; count++)); do
    if compose exec -T n8n wget -qO- \
      http://127.0.0.1:5678/healthz/readiness >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  echo "n8n did not become healthy" >&2
  return 1
}

run_reconcile() {
  compose --profile reconcile run --rm -T n8n-reconcile
}

workflow_id() {
  python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["id"])
PY
}
