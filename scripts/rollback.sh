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
current_revision="${CURRENT_GIT_REVISION:-${DEPLOYED_GIT_REVISION:-}}"
previous_revision="${PREVIOUS_GIT_REVISION:-}"

if [[ -z "$current_tag" || -z "$previous_tag" || -z "$rollback_backup" || \
      -z "$previous_revision" ]]; then
  echo "No complete previous release and backup are recorded." >&2
  exit 1
fi
checkout_revision="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
if [[ -n "$current_revision" && "$checkout_revision" != "$current_revision" ]]; then
  echo "Rollback must run from the current release source: $current_revision" >&2
  exit 1
fi
if [[ "${SYNDICATOR_ALLOW_DIRTY:-0}" != "1" ]] && \
   [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing to roll back from a dirty working tree." >&2
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

source_bundle="$(mktemp -d)"
cleanup() {
  status=$?
  rm -rf "$source_bundle"
  exit "$status"
}
trap cleanup EXIT
git archive "$previous_revision" | tar -x -C "$source_bundle"
if [[ ! -f "$source_bundle/docker-compose.yml" ]]; then
  echo "Previous revision has no deployable Compose definition." >&2
  exit 1
fi

export SYNDICATOR_IMAGE_TAG="$previous_tag"
export SYNDICATOR_SOURCE_ROOT="$source_bundle"
export SYNDICATOR_SOURCE_REVISION="$previous_revision"
"$ROOT/scripts/restore.sh" --yes --no-build "$rollback_backup"
write_release_state \
  "$previous_tag" \
  "$current_tag" \
  "$forward_backup" \
  "$previous_revision" \
  "$current_revision"
trap - EXIT
rm -rf "$source_bundle"

echo "Rolled back from $current_tag to $previous_tag."
