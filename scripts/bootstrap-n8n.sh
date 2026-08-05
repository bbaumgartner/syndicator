#!/usr/bin/env bash
# Import syndicator credentials + workflows into the running compose n8n,
# publish webhook workflows, and smoke-check webhooks + pyautoflip.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example and fill secrets." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
# shellcheck source=/dev/null
source .env
set +a

COMPOSE=(docker compose)
N8N_EXEC=(docker compose exec -T -u node n8n)
SYNDICATOR_WORKFLOWS=(
  "Blog Post Publish"
  "Reel Publish"
  "Adapt Hugo Media"
  "Adapt Feature Image"
  "Adapt Reel Media"
  "Syndicator Error"
)
WEBHOOK_WORKFLOW_IDS=(
  "l7HCCWtO1ALC82n6" # Blog Post Publish
  "zh21miLsQC8Jvua6" # Reel Publish
)

need() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required env: $name" >&2
    exit 1
  fi
}

need N8N_ENCRYPTION_KEY
need N8N_API_KEY
need OPENAI_API_KEY
need POSTIZ_API_KEY
need MAILGUN_API_KEY
need MAILGUN_API_DOMAIN
need MAILGUN_EMAIL_DOMAIN
need SFTP_HOST
need SFTP_USERNAME

if [[ -z "${SFTP_PRIVATE_KEY:-}" ]]; then
  key_file="${SFTP_PRIVATE_KEY_FILE:-./secrets/sftp_n8n_ed25519}"
  if [[ ! -f "$key_file" ]]; then
    echo "Set SFTP_PRIVATE_KEY or provide $key_file" >&2
    exit 1
  fi
  SFTP_PRIVATE_KEY="$(cat "$key_file")"
  export SFTP_PRIVATE_KEY
fi

echo "Waiting for n8n…"
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T n8n wget -qO- http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! "${COMPOSE[@]}" exec -T n8n wget -qO- http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
  echo "n8n did not become healthy" >&2
  exit 1
fi

resolve_owner_user_id() {
  if [[ -n "${N8N_OWNER_USER_ID:-}" ]]; then
    printf '%s' "$N8N_OWNER_USER_ID"
    return
  fi
  local vol
  # compose `name: syndicator` → volume syndicator_n8n_data
  vol="syndicator_n8n_data"
  if ! docker volume inspect "$vol" >/dev/null 2>&1; then
    vol="$(docker volume ls -q | grep -E 'n8n_data$' | head -1 || true)"
  fi
  if [[ -n "$vol" ]]; then
    docker run --rm -v "${vol}:/data:ro" alpine sh -c \
      'apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite "SELECT id FROM user WHERE roleSlug='\''global:owner'\'' LIMIT 1;"' \
      2>/dev/null || true
  fi
}

OWNER_USER_ID="$(resolve_owner_user_id | tr -d '[:space:]')"
if [[ -z "$OWNER_USER_ID" ]]; then
  echo "Could not resolve n8n owner user id. Create the owner account in the UI, then re-run (or set N8N_OWNER_USER_ID)." >&2
  exit 1
fi
echo "Using owner userId=$OWNER_USER_ID"

render_credential() {
  local template="$1"
  local out="$2"
  python3 - "$template" "$out" <<'PY'
import json, os, re, sys
src, dst = sys.argv[1], sys.argv[2]
raw = open(src, encoding="utf-8").read()

def repl(match: re.Match[str]) -> str:
    key = match.group(1)
    if key not in os.environ:
        raise SystemExit(f"Missing env for template: {key}")
    return json.dumps(os.environ[key])[1:-1]  # escape for JSON string context

# Replace ${VAR} inside JSON string values with JSON-escaped content.
rendered = re.sub(r"\$\{([A-Z0-9_]+)\}", repl, raw)
json.loads(rendered)  # validate
open(dst, "w", encoding="utf-8").write(rendered)
PY
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Importing credentials…"
for template in n8n/credentials/*.template.json; do
  base="$(basename "$template" .template.json)"
  rendered="$TMP_DIR/${base}.json"
  render_credential "$template" "$rendered"
  # Copy into container and import (decrypted JSON never stays in the repo).
  docker compose cp "$rendered" "n8n:/tmp/${base}.json"
  "${N8N_EXEC[@]}" n8n import:credentials --input="/tmp/${base}.json" --userId="$OWNER_USER_ID"
  "${N8N_EXEC[@]}" rm -f "/tmp/${base}.json"
done

echo "Importing workflows…"
for name in "${SYNDICATOR_WORKFLOWS[@]}"; do
  file="n8n/workflows/${name}.json"
  if [[ ! -f "$file" ]]; then
    echo "Missing workflow export: $file" >&2
    exit 1
  fi
  docker compose cp "$file" "n8n:/tmp/workflow-import.json"
  "${N8N_EXEC[@]}" n8n import:workflow --input="/tmp/workflow-import.json" --userId="$OWNER_USER_ID"
  "${N8N_EXEC[@]}" rm -f /tmp/workflow-import.json
done

echo "Publishing webhook workflows…"
for id in "${WEBHOOK_WORKFLOW_IDS[@]}"; do
  code="$(curl -sS -o /tmp/n8n-activate-body -w '%{http_code}' -X POST \
    -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
    -H "Content-Type: application/json" \
    "http://127.0.0.1:${N8N_HOST_PORT:-5678}/api/v1/workflows/${id}/publish" || true)"
  if [[ "$code" != "200" ]]; then
    # Fallback for older n8n builds
    code="$(curl -sS -o /tmp/n8n-activate-body -w '%{http_code}' -X POST \
      -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
      -H "Content-Type: application/json" \
      "http://127.0.0.1:${N8N_HOST_PORT:-5678}/api/v1/workflows/${id}/activate" || true)"
  fi
  if [[ "$code" != "200" ]]; then
    echo "Failed to publish workflow $id (HTTP $code): $(cat /tmp/n8n-activate-body)" >&2
    exit 1
  fi
  echo "  published $id"
done

echo "Verifying production webhooks…"
for path in /webhook/publish /webhook/reel; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' \
    -d '{}' \
    "http://127.0.0.1:${N8N_HOST_PORT:-5678}${path}" || true)"
  if [[ "$code" == "404" ]]; then
    echo "Webhook $path returned 404 — workflow likely inactive or path mismatch" >&2
    exit 1
  fi
  echo "  $path → HTTP $code"
done

echo "Verifying pyautoflip from n8n network…"
health="$("${COMPOSE[@]}" exec -T n8n wget -qO- http://pyautoflip:8080/health || true)"
if [[ "$health" != *'"status":"ok"'* && "$health" != *'"status": "ok"'* ]]; then
  echo "pyautoflip /health failed: $health" >&2
  exit 1
fi
echo "  pyautoflip /health → $health"

echo "Bootstrap complete."
