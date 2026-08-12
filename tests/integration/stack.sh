#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

tmp="$(mktemp -d)"
project="syndicator-it-${RANDOM}"
env_file="$tmp/integration.env"

free_port() {
  python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

n8n_port="$(free_port)"
sftp_port="$(free_port)"

cat >"$env_file" <<EOF
GENERIC_TIMEZONE=UTC
N8N_HOST=127.0.0.1
N8N_PORT=5678
N8N_PROTOCOL=http
N8N_HOST_PORT=$n8n_port
N8N_WEBHOOK_URL=http://127.0.0.1:$n8n_port/
N8N_SECURE_COOKIE=false
N8N_ENCRYPTION_KEY=integration-only-encryption-key
N8N_OWNER_EMAIL=ci@example.invalid
N8N_OWNER_PASSWORD=ci-owner-password
N8N_OWNER_ENV_FILE=$ROOT/tests/fixtures/n8n_owner.env
N8N_API_KEY_FILE=$tmp/n8n_api_key
N8N_BOOTSTRAP_STATE_FILE=$tmp/bootstrap.sha256
OPENAI_API_KEY=integration-openai-key
POSTIZ_API_KEY=integration-postiz-key
SFTP_PUBLISH_PORT=$sftp_port
SFTP_HOST=sftp
SFTP_USERNAME=sftp
SFTP_PRIVATE_KEY_FILE=$tmp/sftp_n8n_ed25519
SFTP_KEYS_DIR=$tmp/keys
PYAUTOFLIP_WARM_MODELS=0
SYNDICATOR_BACKUP_DIR=$tmp/backups
SYNDICATOR_RELEASE_STATE_FILE=$tmp/release.env
EOF
chmod 600 "$env_file"

export SYNDICATOR_ENV_FILE="$env_file"
export SYNDICATOR_PROJECT="$project"

cleanup() {
  status=$?
  if [[ "$status" -ne 0 ]]; then
    docker compose --env-file "$env_file" -p "$project" \
      logs --no-color >&2 || true
  fi
  docker compose --env-file "$env_file" -p "$project" \
    down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$tmp"
  exit "$status"
}
trap cleanup EXIT

"$ROOT/bin/syndicator" deploy

cp "$tmp/n8n_api_key" "$tmp/n8n_api_key.before"
if ! "$ROOT/bin/syndicator" deploy | tee "$tmp/second-deploy.log"; then
  exit 1
fi
if ! python3 - "$tmp/second-deploy.log" <<'PY'
from pathlib import Path
import sys

raise SystemExit(0 if "already current" in Path(sys.argv[1]).read_text() else 1)
PY
then
  echo "Second deployment did not skip an unchanged bootstrap." >&2
  exit 1
fi
cmp "$tmp/n8n_api_key.before" "$tmp/n8n_api_key"

api_key="$(tr -d '[:space:]' <"$tmp/n8n_api_key")"
curl -fsS \
  -H "X-N8N-API-KEY: $api_key" \
  "http://127.0.0.1:${n8n_port}/api/v1/workflows?limit=100" |
  python3 -c '
import json,sys
body=json.load(sys.stdin)
items=body.get("data", body)
if isinstance(items, dict):
    items=items.get("data", [])
expected={
    "8NOGn9jgOoV0fw0u",
    "OGa6Xa8GxkSmA7Cr",
    "y9TTx7N8Iygn88ry",
    "l7HCCWtO1ALC82n6",
    "zh21miLsQC8Jvua6",
}
actual={item["id"] for item in items if item.get("id") in expected}
if actual != expected:
    raise SystemExit(f"Expected five Syndicator workflows, got {sorted(actual)}")
'

printf '%s\n' "integration payload" >"$tmp/upload.txt"
ssh-keyscan -p "$sftp_port" 127.0.0.1 >"$tmp/known_hosts" 2>/dev/null
cat >"$tmp/sftp.batch" <<EOF
put $tmp/upload.txt syndicator/restore-marker.txt
get syndicator/restore-marker.txt $tmp/download.txt
quit
EOF
sftp -q -b "$tmp/sftp.batch" \
  -P "$sftp_port" \
  -i "$tmp/sftp_n8n_ed25519" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$tmp/known_hosts" \
  sftp@127.0.0.1
cmp "$tmp/upload.txt" "$tmp/download.txt"

backup_archive="$tmp/backups/integration.tar.gz"
"$ROOT/bin/syndicator" backup --output "$backup_archive"
cat >"$tmp/sftp-remove.batch" <<EOF
rm syndicator/restore-marker.txt
quit
EOF
sftp -q -b "$tmp/sftp-remove.batch" \
  -P "$sftp_port" \
  -i "$tmp/sftp_n8n_ed25519" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$tmp/known_hosts" \
  sftp@127.0.0.1

"$ROOT/bin/syndicator" restore --yes --no-build "$backup_archive"
cat >"$tmp/sftp-restored.batch" <<EOF
get syndicator/restore-marker.txt $tmp/restored.txt
rm syndicator/restore-marker.txt
quit
EOF
sftp -q -b "$tmp/sftp-restored.batch" \
  -P "$sftp_port" \
  -i "$tmp/sftp_n8n_ed25519" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$tmp/known_hosts" \
  sftp@127.0.0.1
cmp "$tmp/upload.txt" "$tmp/restored.txt"

initial_tag="$(git rev-parse --short=12 HEAD)"
"$ROOT/bin/syndicator" update --tag integration-next
"$ROOT/bin/syndicator" rollback
# shellcheck source=/dev/null
source "$tmp/release.env"
if [[ "${CURRENT_TAG:-}" != "$initial_tag" || \
      "${PREVIOUS_TAG:-}" != "integration-next" ]]; then
  echo "Rollback release state is incorrect." >&2
  exit 1
fi

echo "Isolated stack integration test passed."
