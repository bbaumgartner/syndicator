#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

load_env
wait_for_n8n 30 2

if [[ -z "${N8N_API_KEY:-}" ]]; then
  api_key_file="$(resolve_from_root "${N8N_API_KEY_FILE:-secrets/n8n_api_key}")"
  if [[ ! -s "$api_key_file" ]]; then
    echo "Missing n8n API key: $api_key_file" >&2
    exit 1
  fi
  N8N_API_KEY="$(tr -d '[:space:]' <"$api_key_file")"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
n8n_base="http://127.0.0.1:${N8N_HOST_PORT:-5678}"

for file in n8n/workflows/*.json; do
  id="$(workflow_id "$file")"
  body="$tmp/workflow-${id}.json"
  code="$(curl -sS -o "$body" -w '%{http_code}' \
    -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
    "${n8n_base}/api/v1/workflows/${id}" || true)"
  if [[ "$code" != "200" ]]; then
    echo "Workflow $id is unavailable through the n8n API (HTTP $code)." >&2
    exit 1
  fi
  python3 - "$body" "$file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    body = json.load(handle)
data = body.get("data", body)
if data.get("active") is not True:
    raise SystemExit(f"{sys.argv[2]} is not active")
PY
done
echo "n8n health and workflow publication are valid."

health="$(compose exec -T n8n wget -qO- http://pyautoflip:8080/health || true)"
if [[ "$health" != *'"status":"ok"'* && "$health" != *'"status": "ok"'* ]]; then
  echo "pyautoflip health check failed: $health" >&2
  exit 1
fi
echo "pyautoflip is reachable from n8n."

private_key="$(resolve_from_root "${SFTP_PRIVATE_KEY_FILE:-secrets/sftp_n8n_ed25519}")"
sftp_port="${SFTP_PUBLISH_PORT:-2222}"
ssh-keyscan -p "$sftp_port" 127.0.0.1 >"$tmp/known_hosts" 2>/dev/null
printf 'pwd\nquit\n' | sftp -q -b - \
  -P "$sftp_port" \
  -i "$private_key" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$tmp/known_hosts" \
  "${SFTP_USERNAME}@127.0.0.1" >/dev/null
echo "SFTP key authentication is valid."

echo "Syndicator verification complete."
