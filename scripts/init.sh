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

status=0
python3 - "$ENV_FILE" <<'PY' || status=$?
from pathlib import Path
import secrets
import sys

path = Path(sys.argv[1])
required = (
    "N8N_ENCRYPTION_KEY",
    "N8N_OWNER_EMAIL",
    "N8N_OWNER_PASSWORD",
    "OPENAI_API_KEY",
    "POSTIZ_API_KEY",
)
values: dict[str, str] = {}
lines = path.read_text(encoding="utf-8").splitlines()
key_index = None
for index, line in enumerate(lines):
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    name = name.strip()
    values[name] = value
    if name == "N8N_ENCRYPTION_KEY":
        key_index = index

if not values.get("N8N_ENCRYPTION_KEY"):
    generated = secrets.token_hex(32)
    replacement = f"N8N_ENCRYPTION_KEY={generated}"
    if key_index is None:
        lines.append(replacement)
    else:
        lines[key_index] = replacement
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    values["N8N_ENCRYPTION_KEY"] = generated
    print(f"Generated N8N_ENCRYPTION_KEY in {path}")

missing = [name for name in required if not values.get(name)]
if missing:
    for name in missing:
        print(f"Missing required environment value: {name}", file=sys.stderr)
    raise SystemExit(2)
PY
if [[ "$status" -eq 2 ]]; then
  if [[ "$created" -eq 1 ]]; then
    echo "Created $ENV_FILE. Fill the remaining values, then run init again." >&2
  fi
  exit 2
elif [[ "$status" -ne 0 ]]; then
  exit "$status"
fi

echo "Initialization is complete."
