#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

created=0
if [[ ! -f "$ENV_FILE" ]]; then
  mkdir -p "$(dirname "$ENV_FILE")"
  cp "$ROOT/.env.example" "$ENV_FILE"
  created=1
fi
chmod 600 "$ENV_FILE"

load_env
if [[ -z "${N8N_ENCRYPTION_KEY:-}" ]]; then
  encryption_key="$(openssl rand -hex 32)"
  python3 - "$ENV_FILE" "$encryption_key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
replacement = f"N8N_ENCRYPTION_KEY={sys.argv[2]}"
lines = path.read_text(encoding="utf-8").splitlines()
for index, line in enumerate(lines):
    if line.startswith("N8N_ENCRYPTION_KEY="):
        lines[index] = replacement
        break
else:
    lines.append(replacement)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
  load_env
  echo "Generated N8N_ENCRYPTION_KEY in $ENV_FILE"
fi

missing=0
for name in \
  N8N_OWNER_EMAIL \
  N8N_OWNER_PASSWORD \
  OPENAI_API_KEY \
  POSTIZ_API_KEY \
  SFTP_HOST \
  SFTP_USERNAME; do
  if ! need_env "$name"; then
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  if [[ "$created" -eq 1 ]]; then
    echo "Created $ENV_FILE. Fill the values above, then run init again." >&2
  fi
  exit 2
fi

"$ROOT/scripts/ensure-sftp-keys.sh"
"$ROOT/scripts/ensure-n8n-owner.sh"

echo "Initialization is complete."
