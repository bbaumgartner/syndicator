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
current_source_root="${CURRENT_SOURCE_ROOT:-}"
previous_source_root="${PREVIOUS_SOURCE_ROOT:-}"

if [[ -z "$current_tag" || -z "$previous_tag" || -z "$rollback_backup" || \
      -z "$previous_revision" ]]; then
  echo "No complete previous release and backup are recorded." >&2
  exit 1
fi
checkout_revision="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
if [[ "$checkout_revision" != "$current_revision" && \
      "$checkout_revision" != "$previous_revision" ]]; then
  echo "Checkout $checkout_revision is unrelated to the retained releases." >&2
  exit 1
fi
if [[ "${SYNDICATOR_ALLOW_DIRTY:-0}" != "1" ]] && \
   [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing to roll back from a dirty working tree." >&2
  exit 1
fi
if [[ -z "$current_source_root" || ! -d "$current_source_root" ]]; then
  current_source_root="$(materialize_release_source "$current_revision")"
fi
if [[ -z "$previous_source_root" || ! -d "$previous_source_root" ]]; then
  previous_source_root="$(materialize_release_source "$previous_revision")"
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
export SYNDICATOR_SOURCE_ROOT="$current_source_root"
SOURCE_ROOT="$current_source_root"
backup_dir="$(resolve_from_root "${SYNDICATOR_BACKUP_DIR:-backups}")"
forward_backup="$backup_dir/pre-rollback-${current_tag}-to-${previous_tag}-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
"$ROOT/scripts/backup.sh" --output "$forward_backup"

if [[ ! -f "$previous_source_root/docker-compose.yml" ]]; then
  echo "Previous revision has no deployable Compose definition." >&2
  exit 1
fi

export SYNDICATOR_IMAGE_TAG="$previous_tag"
export SYNDICATOR_SOURCE_ROOT="$previous_source_root"
SOURCE_ROOT="$previous_source_root"
export SYNDICATOR_SOURCE_REVISION="$previous_revision"
"$ROOT/scripts/restore.sh" --yes --no-build "$rollback_backup"
write_release_state \
  "$previous_tag" \
  "$current_tag" \
  "$forward_backup" \
  "$previous_revision" \
  "$current_revision" \
  "$previous_source_root" \
  "$current_source_root"

echo "Rolled back from $current_tag to $previous_tag."
