#!/usr/bin/env bash
# Deploy the reviewed, pinned sources in the current checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/deploy.sh" --pull "$@"
