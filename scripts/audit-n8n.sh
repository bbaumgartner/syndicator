#!/usr/bin/env bash
# Audit n8n community-node lockfile, allowing known false-positive advisories.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/n8n"

# GHSA-mrhx-c7xc-m3w5 marks every zod <4.6.6. n8n pins zod 3.x; CVE-2023-54404
# was rejected and there is no zod 3 fix. Force-upgrading to zod 4 breaks n8n.
ALLOWLIST='GHSA-mrhx-c7xc-m3w5'

set +e
human="$(npm audit 2>&1)"
audit_status=$?
json="$(npm audit --json 2>/dev/null)"
set -e

printf '%s\n' "$human"

if [[ "$audit_status" -eq 0 ]]; then
  exit 0
fi

node --input-type=module -e '
const report = JSON.parse(process.argv[1]);
const allow = new Set(process.argv[2].split(/\s+/).filter(Boolean));
const blocked = [];

for (const entry of Object.values(report.vulnerabilities || {})) {
  for (const item of entry.via || []) {
    if (typeof item === "string") continue;
    const id = String(item.url || "").split("/").pop();
    if (!id || allow.has(id)) continue;
    blocked.push({
      name: entry.name,
      severity: item.severity || entry.severity,
      id,
      title: item.title,
      url: item.url,
    });
  }
}

if (blocked.length > 0) {
  console.error("npm audit failed after allowlist:");
  console.error(JSON.stringify(blocked, null, 2));
  process.exit(1);
}

console.error(
  "npm audit: ignored allowlisted advisories only:",
  [...allow].join(", ")
);
' "$json" "$ALLOWLIST"
