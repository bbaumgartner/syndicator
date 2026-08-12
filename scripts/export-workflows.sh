#!/usr/bin/env bash
# Export the syndicator workflows from the running n8n into n8n/workflows/.
# Scoped to the five syndicator workflows; other instance workflows are ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"
load_env

OUT_DIR="$ROOT/n8n/workflows"
mkdir -p "$OUT_DIR"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

count=0
for source in "$OUT_DIR"/*.json; do
  name="$(basename "$source" .json)"
  id="$(workflow_id "$source")"
  echo "Exporting $name ($id)…"
  compose exec -T -u node n8n \
    n8n export:workflow --id="$id" --pretty --output="/tmp/export-${id}.json"
  compose cp "n8n:/tmp/export-${id}.json" "$TMP/${name}.json"
  compose exec -T -u node n8n rm -f "/tmp/export-${id}.json"

  # Normalize a single workflow and remove instance-specific export metadata.
  python3 - "$TMP/${name}.json" "$OUT_DIR/${name}.json" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))
if isinstance(data, list):
    if len(data) != 1:
        raise SystemExit(f"Expected 1 workflow in {src}, got {len(data)}")
    data = data[0]
data["pinData"] = {}
data.pop("shared", None)
data.pop("versionMetadata", None)
if isinstance(data.get("meta"), dict):
    data["meta"].pop("instanceId", None)
json.dump(data, open(dst, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
open(dst, "a", encoding="utf-8").write("\n")
PY
  count=$((count + 1))
done

echo "Wrote $count workflows to $OUT_DIR"
