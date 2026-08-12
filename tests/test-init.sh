#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

set +e
SYNDICATOR_ENV_FILE="$tmp/.env" "$ROOT/bin/syndicator" init >/dev/null 2>&1
status=$?
set -e

if [[ "$status" -ne 2 ]]; then
  echo "First init should request configuration and exit 2, got $status." >&2
  exit 1
fi

python3 - "$tmp/.env" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
values = {}
for line in path.read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value
key = values.get("N8N_ENCRYPTION_KEY", "")
if len(key) != 64:
    raise SystemExit("init did not generate a 256-bit n8n encryption key")
PY

echo "First-run initialization test passed."
