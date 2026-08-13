#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

load_env
wait_for_n8n 30 2
run_reconcile

echo "n8n health and workflow publication are valid."

health="$(compose exec -T n8n wget -qO- http://pyautoflip:8080/health || true)"
if [[ "$health" != *'"status":"ok"'* && "$health" != *'"status": "ok"'* ]]; then
  echo "pyautoflip health check failed: $health" >&2
  exit 1
fi
echo "pyautoflip is reachable from n8n."

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
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
