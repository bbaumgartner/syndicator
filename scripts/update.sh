#!/usr/bin/env bash
# Rebuild n8n + pyautoflip from the latest base images and recreate when changed.
# Safe to re-run (volumes untouched). Intended for cron / systemd timer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG="${UPDATE_LOG:-$ROOT/update.log}"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

echo "=== $(date -Is) starting syndicator update ==="

n8n_old="$(docker image inspect syndicator-n8n:stable --format '{{.Id}}' 2>/dev/null || true)"
py_old="$(docker image inspect syndicator-pyautoflip:local --format '{{.Id}}' 2>/dev/null || true)"

docker compose build --pull
docker compose up -d --remove-orphans

n8n_new="$(docker image inspect syndicator-n8n:stable --format '{{.Id}}' 2>/dev/null || true)"
py_new="$(docker image inspect syndicator-pyautoflip:local --format '{{.Id}}' 2>/dev/null || true)"

changed=0
if [[ "$n8n_old" != "$n8n_new" ]]; then
  echo "Updated syndicator-n8n:stable"
  echo "  old: ${n8n_old:-<none>}"
  echo "  new: $n8n_new"
  changed=1
else
  echo "n8n image unchanged (${n8n_new:-<none>})"
fi

if [[ "$py_old" != "$py_new" ]]; then
  echo "Updated syndicator-pyautoflip:local"
  echo "  old: ${py_old:-<none>}"
  echo "  new: $py_new"
  changed=1
else
  echo "pyautoflip image unchanged (${py_new:-<none>})"
fi

if [[ "$changed" -eq 1 ]]; then
  docker image prune -f >/dev/null
fi

echo "=== $(date -Is) done ==="
