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

mkdir -p "$tmp/keys"
ssh-keygen -t ed25519 -f "$tmp/sftp_client" -N '' -C 'syndicator-it' </dev/null
cp "$tmp/sftp_client.pub" "$tmp/keys/client.pub"
rm -f "$tmp/sftp_client.pub"
export SFTP_CLIENT_KEY_FILE="$tmp/sftp_client"

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
OPENAI_API_KEY=integration-openai-key
POSTIZ_API_KEY=integration-postiz-key
SFTP_PUBLISH_PORT=$sftp_port
SFTP_KEYS_DIR=$tmp/keys
SFTP_CLIENT_KEY_FILE=$tmp/sftp_client
PYAUTOFLIP_WARM_MODELS=0
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

test_failed_deployment() {
  printf '%s\n' 'SYNDICATOR_TEST_FAIL_AFTER_START=1' >>"$env_file"
  set +e
  "$ROOT/bin/syndicator" deploy \
    >"$tmp/failed-deploy.log" 2>&1
  failed_status=$?
  set -e
  if [[ "$failed_status" -eq 0 ]]; then
    echo "Deliberately invalid deployment unexpectedly succeeded." >&2
    exit 1
  fi
  if [[ -n "$(docker compose --env-file "$env_file" -p "$project" \
    ps --status running -q n8n)" ]]; then
    echo "Failed deployment left unverified n8n running." >&2
    exit 1
  fi
}

"$ROOT/bin/syndicator" deploy
if [[ "${SYNDICATOR_INTEGRATION_FAILURE_ONLY:-0}" == "1" ]]; then
  test_failed_deployment
  echo "Failed deployment containment test passed."
  exit 0
fi

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

printf '%s\n' "integration payload" >"$tmp/upload.txt"
ssh-keyscan -p "$sftp_port" 127.0.0.1 >"$tmp/known_hosts" 2>/dev/null
cat >"$tmp/sftp.batch" <<EOF
put $tmp/upload.txt syndicator/upload.txt
get syndicator/upload.txt $tmp/download.txt
rm syndicator/upload.txt
quit
EOF
sftp -q -b "$tmp/sftp.batch" \
  -P "$sftp_port" \
  -i "$tmp/sftp_client" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$tmp/known_hosts" \
  sftp@127.0.0.1
cmp "$tmp/upload.txt" "$tmp/download.txt"

test_failed_deployment

echo "Isolated stack integration test passed."
