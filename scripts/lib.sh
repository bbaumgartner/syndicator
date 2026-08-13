#!/usr/bin/env bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="${SYNDICATOR_SOURCE_ROOT:-$ROOT}"
ENV_FILE="${SYNDICATOR_ENV_FILE:-$ROOT/.env}"
SYNDICATOR_LOADED_ENV_KEYS=()

cd "$ROOT" || exit 1

load_env() {
  local parsed key value
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing environment file: $ENV_FILE" >&2
    return 1
  fi
  for key in "${SYNDICATOR_LOADED_ENV_KEYS[@]+"${SYNDICATOR_LOADED_ENV_KEYS[@]}"}"; do
    unset "$key"
  done
  SYNDICATOR_LOADED_ENV_KEYS=()
  parsed="$(mktemp)"
  if ! python3 "$ROOT/scripts/dotenv.py" "$ENV_FILE" >"$parsed"; then
    rm -f "$parsed"
    return 1
  fi
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    export "$key=$value"
    SYNDICATOR_LOADED_ENV_KEYS+=("$key")
  done <"$parsed"
  rm -f "$parsed"
}

need_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment value: $name" >&2
    return 1
  fi
}

resolve_from_root() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s\n' "$ROOT/${path#./}"
  fi
}

release_state_file() {
  resolve_from_root "${SYNDICATOR_RELEASE_STATE_FILE:-secrets/release.env}"
}

pending_release_file() {
  printf '%s.pending\n' "$(release_state_file)"
}

load_release_state() {
  local state
  state="$(release_state_file)"
  if [[ -s "$state" ]]; then
    # shellcheck source=/dev/null
    source "$state"
  fi
}

write_release_state() {
  local current="$1"
  local current_revision="${2:-}"
  local state temporary_state
  if [[ -z "$current_revision" ]]; then
    current_revision="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
  fi
  state="$(release_state_file)"
  mkdir -p "$(dirname "$state")"
  umask 077
  temporary_state="${state}.tmp.$$"
  {
    printf 'CURRENT_TAG=%q\n' "$current"
    printf 'CURRENT_GIT_REVISION=%q\n' "$current_revision"
    printf 'DEPLOYED_AT=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$temporary_state"
  chmod 600 "$temporary_state"
  mv "$temporary_state" "$state"
}

compose() {
  local args=(
    --project-directory "$SOURCE_ROOT"
    -f "$SOURCE_ROOT/docker-compose.yml"
    --env-file "$ENV_FILE"
  )
  if [[ -z "${SYNDICATOR_IMAGE_TAG:-}" ]]; then
    load_release_state
    if [[ -n "${CURRENT_TAG:-}" ]]; then
      export SYNDICATOR_IMAGE_TAG="$CURRENT_TAG"
    fi
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
