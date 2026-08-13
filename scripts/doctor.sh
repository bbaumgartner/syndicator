#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

require_config=0
if [[ "${1:-}" == "--require-config" ]]; then
  require_config=1
elif [[ "$#" -gt 0 ]]; then
  echo "Usage: $0 [--require-config]" >&2
  exit 2
fi

failed=0
for command in docker curl python3 openssl ssh-keygen sftp; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but its daemon is unavailable." >&2
  exit 1
fi
docker compose version >/dev/null

if [[ "$require_config" -eq 0 ]]; then
  echo "Host prerequisites are available."
  exit 0
fi

load_env
for name in \
  N8N_ENCRYPTION_KEY \
  N8N_OWNER_EMAIL \
  N8N_OWNER_PASSWORD \
  OPENAI_API_KEY \
  POSTIZ_API_KEY; do
  need_env "$name"
done

compose config --quiet

if [[ "${N8N_HOST:-localhost}" != "localhost" && \
      "${N8N_HOST:-localhost}" != "127.0.0.1" && \
      "${N8N_HOST:-localhost}" != "::1" && \
      "${N8N_PROTOCOL:-http}" != "https" ]]; then
  echo "Warning: n8n is configured for non-local HTTP without TLS." >&2
fi

echo "Host and configuration are valid."
