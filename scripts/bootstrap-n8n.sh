#!/usr/bin/env bash
# Idempotently import credentials and workflows into a running n8n instance.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

load_env
for name in \
  N8N_ENCRYPTION_KEY \
  N8N_OWNER_EMAIL \
  N8N_OWNER_PASSWORD \
  OPENAI_API_KEY \
  POSTIZ_API_KEY \
  SFTP_HOST \
  SFTP_USERNAME; do
  need_env "$name"
done

WORKFLOW_FILES=(
  "n8n/workflows/Adapt Hugo Media.json"
  "n8n/workflows/Adapt Feature Image.json"
  "n8n/workflows/Adapt Reel Media.json"
  "n8n/workflows/Blog Post Publish.json"
  "n8n/workflows/Reel Publish.json"
)

if [[ -z "${SFTP_PRIVATE_KEY:-}" ]]; then
  key_file="$(resolve_from_root "${SFTP_PRIVATE_KEY_FILE:-secrets/sftp_n8n_ed25519}")"
  if [[ ! -f "$key_file" ]]; then
    echo "Missing SFTP private key: $key_file" >&2
    exit 1
  fi
  SFTP_PRIVATE_KEY="$(<"$key_file")"
  export SFTP_PRIVATE_KEY
fi

wait_for_n8n

N8N_BASE="http://127.0.0.1:${N8N_HOST_PORT:-5678}"
API_KEY_LABEL="syndicator-bootstrap"
API_KEY_FILE="$(resolve_from_root "${N8N_API_KEY_FILE:-secrets/n8n_api_key}")"
STATE_FILE="$(resolve_from_root "${N8N_BOOTSTRAP_STATE_FILE:-secrets/bootstrap.sha256}")"
TMP_DIR="$(mktemp -d)"
COOKIE_JAR="$TMP_DIR/n8n-cookies.txt"
LOGIN_BODY="$TMP_DIR/login.json"
trap 'rm -rf "$TMP_DIR"' EXIT

login_n8n() {
  if [[ -s "$LOGIN_BODY" ]]; then
    return
  fi

  local code
  code="$(curl -sS -o "$LOGIN_BODY" -w '%{http_code}' \
    -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    -X POST \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,os; print(json.dumps({"emailOrLdapLoginId":os.environ["N8N_OWNER_EMAIL"],"password":os.environ["N8N_OWNER_PASSWORD"]}))')" \
    "${N8N_BASE}/rest/login" || true)"
  if [[ "$code" != "200" ]]; then
    echo "n8n login failed (HTTP $code): $(<"$LOGIN_BODY")" >&2
    exit 1
  fi
}

owner_user_id() {
  login_n8n
  python3 - "$LOGIN_BODY" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    body = json.load(handle)
data = body.get("data", body)
user_id = data.get("id", "")
if not user_id:
    raise SystemExit(f"Login response has no owner id: {body!r}")
print(user_id)
PY
}

api_key_valid() {
  local key="$1"
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "X-N8N-API-KEY: $key" \
    "${N8N_BASE}/api/v1/workflows?limit=1" || true)"
  [[ "$code" == "200" ]]
}

provision_api_key() {
  login_n8n

  local scopes_json key_id raw_key create_body
  scopes_json="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    "${N8N_BASE}/rest/api-keys/scopes")"
  scopes_json="$(python3 -c '
import json,sys
body=json.load(sys.stdin)
scopes=body.get("data", body)
if not isinstance(scopes, list):
    raise SystemExit(f"Unexpected scopes response: {body!r}")
print(json.dumps(scopes))
' <<<"$scopes_json")"

  key_id="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    --get \
    --data-urlencode "label=${API_KEY_LABEL}" \
    --data-urlencode "ownership=mine" \
    --data-urlencode "take=50" \
    "${N8N_BASE}/rest/api-keys" | python3 -c '
import json,sys
body=json.load(sys.stdin)
payload=body.get("data", body)
items=payload.get("items", payload.get("data", [])) if isinstance(payload, dict) else payload
for item in items or []:
    if item.get("label")==sys.argv[1]:
        print(item.get("id",""))
        break
' "$API_KEY_LABEL")"

  if [[ -n "$key_id" ]]; then
    echo "Replacing inaccessible bootstrap API key..."
    raw_key="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -X POST \
      "${N8N_BASE}/rest/api-keys/${key_id}/rotate" | python3 -c '
import json,sys
body=json.load(sys.stdin)
data=body.get("data", body)
key=data.get("rawApiKey") or data.get("apiKey") or ""
if not key or key.startswith("*"):
    raise SystemExit(f"Rotate did not return a raw API key: {body!r}")
print(key)
')"
  else
    echo "Creating bootstrap API key..."
    create_body="$(python3 -c '
import json,sys
print(json.dumps({"label":sys.argv[2],"expiresAt":None,"scopes":json.loads(sys.argv[1])}))
' "$scopes_json" "$API_KEY_LABEL")"
    raw_key="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
      -X POST \
      -H 'Content-Type: application/json' \
      -d "$create_body" \
      "${N8N_BASE}/rest/api-keys" | python3 -c '
import json,sys
body=json.load(sys.stdin)
data=body.get("data", body)
key=data.get("rawApiKey") or ""
if not key:
    raise SystemExit(f"Create did not return a raw API key: {body!r}")
print(key)
')"
  fi

  mkdir -p "$(dirname "$API_KEY_FILE")"
  umask 077
  printf '%s\n' "$raw_key" >"$API_KEY_FILE"
  chmod 600 "$API_KEY_FILE"
  N8N_API_KEY="$raw_key"
  export N8N_API_KEY
}

ensure_api_key() {
  if [[ -n "${N8N_API_KEY:-}" ]]; then
    if ! api_key_valid "$N8N_API_KEY"; then
      echo "N8N_API_KEY is set but is not accepted by n8n." >&2
      exit 1
    fi
    return
  fi

  if [[ -s "$API_KEY_FILE" ]]; then
    N8N_API_KEY="$(tr -d '[:space:]' <"$API_KEY_FILE")"
    export N8N_API_KEY
    if api_key_valid "$N8N_API_KEY"; then
      return
    fi
    unset N8N_API_KEY
  fi

  provision_api_key
}

bootstrap_fingerprint() {
  python3 - <<'PY'
import glob
import hashlib
import os

digest = hashlib.sha256()
for pattern in ("n8n/credentials/*.template.json", "n8n/workflows/*.json"):
    for path in sorted(glob.glob(pattern)):
        digest.update(path.encode())
        with open(path, "rb") as handle:
            digest.update(handle.read())
for name in (
    "OPENAI_API_KEY",
    "POSTIZ_API_KEY",
    "SFTP_HOST",
    "SFTP_USERNAME",
    "SFTP_PRIVATE_KEY",
):
    digest.update(name.encode())
    digest.update(os.environ[name].encode())
print(digest.hexdigest())
PY
}

workflow_is_active() {
  local id="$1"
  local body="$TMP_DIR/workflow-${id}.json"
  local code
  code="$(curl -sS -o "$body" -w '%{http_code}' \
    -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
    "${N8N_BASE}/api/v1/workflows/${id}" || true)"
  [[ "$code" == "200" ]] || return 1
  python3 - "$body" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    body = json.load(handle)
data = body.get("data", body)
raise SystemExit(0 if data.get("active") is True else 1)
PY
}

all_workflows_active() {
  local file id
  for file in "${WORKFLOW_FILES[@]}"; do
    id="$(workflow_id "$ROOT/$file")"
    workflow_is_active "$id" || return 1
  done
}

render_credential() {
  local template="$1"
  local out="$2"
  python3 - "$template" "$out" <<'PY'
import json
import os
import re
import sys

source, destination = sys.argv[1:]
raw = open(source, encoding="utf-8").read()

def replace(match: re.Match[str]) -> str:
    key = match.group(1)
    if key not in os.environ:
        raise SystemExit(f"Missing environment value for template: {key}")
    return json.dumps(os.environ[key])[1:-1]

rendered = re.sub(r"\$\{([A-Z0-9_]+)\}", replace, raw)
json.loads(rendered)
open(destination, "w", encoding="utf-8").write(rendered)
PY
}

copy_into_n8n() {
  local source="$1"
  local destination="$2"
  # shellcheck disable=SC2016
  compose exec -T -u node n8n sh -c 'cat > "$1"' sh "$destination" <"$source"
}

publish_workflow() {
  local id="$1"
  local body="$TMP_DIR/publish-${id}.json"
  local code
  code="$(curl -sS -o "$body" -w '%{http_code}' -X POST \
    -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
    -H 'Content-Type: application/json' \
    "${N8N_BASE}/api/v1/workflows/${id}/publish" || true)"
  if [[ "$code" != "200" ]]; then
    code="$(curl -sS -o "$body" -w '%{http_code}' -X POST \
      -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
      -H 'Content-Type: application/json' \
      "${N8N_BASE}/api/v1/workflows/${id}/activate" || true)"
  fi
  if [[ "$code" != "200" ]]; then
    echo "Failed to publish workflow $id (HTTP $code): $(<"$body")" >&2
    exit 1
  fi
}

ensure_api_key
fingerprint="$(bootstrap_fingerprint)"
if [[ -s "$STATE_FILE" ]] && [[ "$(<"$STATE_FILE")" == "$fingerprint" ]] && \
   all_workflows_active; then
  echo "n8n bootstrap is already current."
  exit 0
fi

OWNER_USER_ID="$(owner_user_id)"
echo "Importing credentials for owner $OWNER_USER_ID..."
for template in n8n/credentials/*.template.json; do
  base="$(basename "$template" .template.json)"
  rendered="$TMP_DIR/${base}.json"
  render_credential "$template" "$rendered"
  copy_into_n8n "$rendered" "/tmp/${base}.json"
  compose exec -T -u node n8n \
    n8n import:credentials --input="/tmp/${base}.json" --userId="$OWNER_USER_ID"
  compose exec -T -u node n8n rm -f "/tmp/${base}.json"
done

echo "Importing and publishing workflows..."
for file in "${WORKFLOW_FILES[@]}"; do
  id="$(workflow_id "$ROOT/$file")"
  copy_into_n8n "$ROOT/$file" /tmp/syndicator-workflow.json
  compose exec -T -u node n8n \
    n8n import:workflow --input=/tmp/syndicator-workflow.json --userId="$OWNER_USER_ID"
  compose exec -T -u node n8n rm -f /tmp/syndicator-workflow.json
  publish_workflow "$id"
done

if ! all_workflows_active; then
  echo "At least one imported workflow is not active." >&2
  exit 1
fi

mkdir -p "$(dirname "$STATE_FILE")"
umask 077
printf '%s\n' "$fingerprint" >"$STATE_FILE"
chmod 600 "$STATE_FILE"
echo "n8n bootstrap complete."
