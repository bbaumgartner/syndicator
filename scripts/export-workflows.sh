#!/usr/bin/env bash
# Export the syndicator workflows from the running n8n into n8n/workflows/.
# Scoped to the five syndicator workflows; other instance workflows are ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OUT_DIR="$ROOT/n8n/workflows"
mkdir -p "$OUT_DIR"

# id|filename (without .json)
WORKFLOWS=(
  "l7HCCWtO1ALC82n6|Blog Post Publish"
  "zh21miLsQC8Jvua6|Reel Publish"
  "OGa6Xa8GxkSmA7Cr|Adapt Hugo Media"
  "8NOGn9jgOoV0fw0u|Adapt Feature Image"
  "y9TTx7N8Iygn88ry|Adapt Reel Media"
)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

for entry in "${WORKFLOWS[@]}"; do
  id="${entry%%|*}"
  name="${entry#*|}"
  echo "Exporting $name ($id)…"
  docker compose exec -T -u node n8n \
    n8n export:workflow --id="$id" --pretty --output="/tmp/export-${id}.json"
  docker compose cp "n8n:/tmp/export-${id}.json" "$TMP/${name}.json"
  docker compose exec -T -u node n8n rm -f "/tmp/export-${id}.json"

  # n8n may wrap a single workflow in an array — normalize to one object file.
  python3 - "$TMP/${name}.json" "$OUT_DIR/${name}.json" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))
if isinstance(data, list):
    if len(data) != 1:
        raise SystemExit(f"Expected 1 workflow in {src}, got {len(data)}")
    data = data[0]
json.dump(data, open(dst, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
open(dst, "a", encoding="utf-8").write("\n")
PY
done

echo "Wrote ${#WORKFLOWS[@]} workflows to $OUT_DIR"
