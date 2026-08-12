#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

owner_env="$ROOT/secrets/n8n_owner.env"
created_owner_env=0

cleanup() {
  if [[ "$created_owner_env" -eq 1 ]]; then
    rm -f "$owner_env"
  fi
}
trap cleanup EXIT

if [[ ! -e "$owner_env" ]]; then
  mkdir -p "$(dirname "$owner_env")"
  printf '%s\n' 'N8N_INSTANCE_OWNER_PASSWORD_HASH=ci-only' >"$owner_env"
  created_owner_env=1
fi

export N8N_ENCRYPTION_KEY="ci-only-encryption-key"
export N8N_OWNER_EMAIL="ci@example.invalid"

if [[ "$#" -gt 0 ]]; then
  docker compose --env-file /dev/null "$@"
else
  docker compose --env-file /dev/null config --quiet
fi
