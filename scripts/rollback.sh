#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

if [[ "$#" -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi

load_env
load_release_state
current_tag="${CURRENT_TAG:-}"
previous_tag="${PREVIOUS_TAG:-}"
rollback_backup="${ROLLBACK_BACKUP:-}"

if [[ -z "$current_tag" || -z "$previous_tag" || -z "$rollback_backup" ]]; then
  echo "No complete previous release and backup are recorded." >&2
  exit 1
fi
if [[ ! -f "$rollback_backup" ]]; then
  echo "Recorded rollback backup is missing: $rollback_backup" >&2
  exit 1
fi
for image in "syndicator-n8n:$previous_tag" "syndicator-pyautoflip:$previous_tag"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "Previous release image is missing: $image" >&2
    exit 1
  fi
done

export SYNDICATOR_IMAGE_TAG="$current_tag"
backup_dir="$(resolve_from_root "${SYNDICATOR_BACKUP_DIR:-backups}")"
forward_backup="$backup_dir/pre-rollback-${current_tag}-to-${previous_tag}-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
"$ROOT/scripts/backup.sh" --output "$forward_backup"

export SYNDICATOR_IMAGE_TAG="$previous_tag"
"$ROOT/scripts/restore.sh" --yes --no-build "$rollback_backup"
write_release_state "$previous_tag" "$current_tag" "$forward_backup"

echo "Rolled back from $current_tag to $previous_tag."
