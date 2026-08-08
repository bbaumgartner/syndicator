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
# Import order: leaves before parents (same as publish).
SYNDICATOR_WORKFLOWS=(
  "Adapt Hugo Media"
  "Adapt Feature Image"
  "Adapt Reel Media"
  "Blog Post Publish"
  "Reel Publish"
)
# Publish order: n8n 2.x requires referenced sub-workflows to be published
# before parents that call them.
PUBLISH_WORKFLOW_IDS=(
  "OGa6Xa8GxkSmA7Cr" # Adapt Hugo Media
  "8NOGn9jgOoV0fw0u" # Adapt Feature Image
  "y9TTx7N8Iygn88ry" # Adapt Reel Media
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
need N8N_OWNER_EMAIL
need N8N_OWNER_PASSWORD
need OPENAI_API_KEY
need POSTIZ_API_KEY
need SFTP_HOST
need SFTP_USERNAME

# Create n8n↔sftp keypair if missing, and refresh authorized public key.
"$ROOT/scripts/ensure-sftp-keys.sh"
# Bcrypt owner password into secrets/n8n_owner.env for Compose.
"$ROOT/scripts/ensure-n8n-owner.sh"

if [[ -z "${SFTP_PRIVATE_KEY:-}" ]]; then
  key_file="${SFTP_PRIVATE_KEY_FILE:-./secrets/sftp_n8n_ed25519}"
  if [[ ! -f "$key_file" ]]; then
    echo "Set SFTP_PRIVATE_KEY or provide $key_file" >&2
    exit 1
  fi
  SFTP_PRIVATE_KEY="$(cat "$key_file")"
  export SFTP_PRIVATE_KEY
fi

# Restart sftp so sshd-wrapper re-syncs authorized_keys from sftp/keys/.
if [[ -n "$("${COMPOSE[@]}" ps -q sftp 2>/dev/null || true)" ]]; then
  echo "Restarting sftp to pick up authorized keys…"
  "${COMPOSE[@]}" restart sftp
fi
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T sftp pgrep -x sshd >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

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

N8N_BASE="http://127.0.0.1:${N8N_HOST_PORT:-5678}"
API_KEY_LABEL="syndicator-bootstrap"
API_KEY_FILE="$ROOT/secrets/n8n_api_key"
TMP_DIR="$(mktemp -d)"
COOKIE_JAR="$TMP_DIR/n8n-cookies.txt"
trap 'rm -rf "$TMP_DIR"' EXIT

ensure_n8n_api_key() {
  if [[ -n "${N8N_API_KEY:-}" ]]; then
    echo "Using N8N_API_KEY from environment"
    return
  fi
  if [[ -f "$API_KEY_FILE" && -s "$API_KEY_FILE" ]]; then
    N8N_API_KEY="$(tr -d '[:space:]' <"$API_KEY_FILE")"
    if [[ -n "$N8N_API_KEY" ]]; then
      echo "Using API key from $API_KEY_FILE"
      export N8N_API_KEY
      return
    fi
  fi

  echo "Logging into n8n to provision API key…"
  local login_code
  login_code="$(curl -sS -o /tmp/n8n-login-body -w '%{http_code}' -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    -X POST \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,os; print(json.dumps({"emailOrLdapLoginId":os.environ["N8N_OWNER_EMAIL"],"password":os.environ["N8N_OWNER_PASSWORD"]}))')" \
    "${N8N_BASE}/rest/login" || true)"
  if [[ "$login_code" != "200" ]]; then
    echo "n8n login failed (HTTP $login_code): $(cat /tmp/n8n-login-body)" >&2
    echo "Ensure N8N_OWNER_EMAIL/PASSWORD match the env-managed owner and that ensure-n8n-owner.sh ran before compose up." >&2
    exit 1
  fi

  local scopes_json key_id raw_key create_body
  scopes_json="$(curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" "${N8N_BASE}/rest/api-keys/scopes")"
  scopes_json="$(python3 -c '
import json,sys
body=json.load(sys.stdin)
scopes=body.get("data", body)
if not isinstance(scopes, list):
    raise SystemExit(f"Unexpected scopes response: {body!r}")
print(json.dumps(scopes))
' <<<"$scopes_json")"

  key_id="$(curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    --get \
    --data-urlencode "label=${API_KEY_LABEL}" \
    --data-urlencode "ownership=mine" \
    --data-urlencode "take=50" \
    "${N8N_BASE}/rest/api-keys" | python3 -c '
import json,sys
body=json.load(sys.stdin)
payload=body.get("data", body)
if isinstance(payload, dict):
    items=payload.get("items", payload.get("data", []))
elif isinstance(payload, list):
    items=payload
else:
    items=[]
label=sys.argv[1]
for item in items or []:
    if item.get("label")==label:
        print(item.get("id",""))
        break
' "$API_KEY_LABEL")"

  if [[ -n "$key_id" ]]; then
    echo "Rotating API key label=$API_KEY_LABEL id=$key_id…"
    raw_key="$(curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -X POST \
      "${N8N_BASE}/rest/api-keys/${key_id}/rotate" | python3 -c '
import json,sys
body=json.load(sys.stdin)
data=body.get("data", body)
key=data.get("rawApiKey") or data.get("apiKey") or ""
if not key or key.startswith("*"):
    raise SystemExit(f"Rotate did not return rawApiKey: {body!r}")
print(key)
')"
  else
    echo "Creating API key label=$API_KEY_LABEL…"
    create_body="$(python3 -c '
import json,sys
scopes=json.loads(sys.argv[1])
print(json.dumps({"label":sys.argv[2],"expiresAt":None,"scopes":scopes}))
' "$scopes_json" "$API_KEY_LABEL")"
    raw_key="$(curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
      -X POST \
      -H 'Content-Type: application/json' \
      -d "$create_body" \
      "${N8N_BASE}/rest/api-keys" | python3 -c '
import json,sys
body=json.load(sys.stdin)
data=body.get("data", body)
key=data.get("rawApiKey") or ""
if not key:
    raise SystemExit(f"Create did not return rawApiKey: {body!r}")
print(key)
')"
  fi

  mkdir -p "$(dirname "$API_KEY_FILE")"
  umask 077
  printf '%s\n' "$raw_key" >"$API_KEY_FILE"
  chmod 600 "$API_KEY_FILE"
  N8N_API_KEY="$raw_key"
  export N8N_API_KEY
  echo "Wrote $API_KEY_FILE"
}

ensure_n8n_api_key

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
  echo "Could not resolve n8n owner user id. Ensure N8N_INSTANCE_OWNER_* is configured (./scripts/ensure-n8n-owner.sh + compose up), then re-run (or set N8N_OWNER_USER_ID)." >&2
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

publish_workflow() {
  local id="$1"
  local code
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
}

echo "Publishing workflows (sub-workflows before webhook parents)…"
for id in "${PUBLISH_WORKFLOW_IDS[@]}"; do
  publish_workflow "$id"
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
