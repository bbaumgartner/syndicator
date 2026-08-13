#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export N8N_ENCRYPTION_KEY="ci-only-encryption-key"
export N8N_OWNER_EMAIL="ci@example.invalid"
export N8N_OWNER_PASSWORD="ci-owner-password"
export OPENAI_API_KEY="ci-openai-key"
export POSTIZ_API_KEY="ci-postiz-key"

if [[ "$#" -gt 0 ]]; then
  docker compose --env-file /dev/null "$@"
else
  docker compose --env-file /dev/null config --quiet
fi
