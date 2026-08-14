#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

wait_for_n8n 30 2
run_reconcile

echo "n8n health and workflow publication are valid."

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

n8n_published="$(compose port n8n 5678 | head -n1)"
n8n_url="http://${n8n_published}"
mapfile -t webhook_paths < <(
  python3 - "$ROOT/n8n/workflows" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
for path in sorted(root.glob("*.json")):
    workflow = json.loads(path.read_text(encoding="utf-8"))
    for node in workflow.get("nodes") or []:
        if node.get("type") != "n8n-nodes-base.webhook":
            continue
        hook = str((node.get("parameters") or {}).get("path") or "").strip().strip("/")
        if hook:
            print(hook)
PY
)
if [[ "${#webhook_paths[@]}" -eq 0 ]]; then
  echo "No webhook paths found in n8n/workflows." >&2
  exit 1
fi
for path in "${webhook_paths[@]}"; do
  # GET a POST-only production webhook. Live: "Did you mean to make a POST request?"
  # Missing from the live router: "The requested webhook ... is not registered."
  code="$(curl -sS -o "$tmp/webhook-${path}.txt" -w '%{http_code}' \
    "${n8n_url}/webhook/${path}" || true)"
  body="$(<"$tmp/webhook-${path}.txt")"
  if [[ "$code" == "405" ]] ||
    [[ "$body" == *"Did you mean to make a POST request"* ]] ||
    [[ "$body" == *"not registered for GET"* ]]; then
    continue
  fi
  echo "Webhook /webhook/${path} is not registered (HTTP ${code}): ${body}" >&2
  exit 1
done
echo "Publish and reel webhooks are registered."

health="$(compose exec -T n8n wget -qO- http://pyautoflip:8080/health || true)"
if [[ "$health" != *'"status":"ok"'* && "$health" != *'"status": "ok"'* ]]; then
  echo "pyautoflip health check failed: $health" >&2
  exit 1
fi
echo "pyautoflip is reachable from n8n."

client_key="$(resolve_from_root "${SFTP_CLIENT_KEY_FILE:-secrets/sftp_client_ed25519}")"
if [[ ! -f "$client_key" ]]; then
  echo "Skipping SFTP check; no client key at $client_key."
else
  published="$(compose port sftp 22)"
  sftp_port="${published##*:}"
  ssh-keyscan -p "$sftp_port" 127.0.0.1 >"$tmp/known_hosts" 2>/dev/null
  printf 'pwd\nquit\n' | sftp -q -b - \
    -P "$sftp_port" \
    -i "$client_key" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$tmp/known_hosts" \
    "sftp@127.0.0.1" >/dev/null
  echo "SFTP key authentication is valid."
fi

echo "Syndicator verification complete."
