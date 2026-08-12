#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

pull=0
backup_on_change=1
requested_tag=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pull)
      pull=1
      ;;
    --no-backup)
      backup_on_change=0
      ;;
    --tag)
      if [[ -z "${2:-}" ]]; then
        echo "--tag requires a value." >&2
        exit 2
      fi
      requested_tag="$2"
      shift
      ;;
    *)
      echo "Usage: $0 [--pull] [--no-backup] [--tag TAG]" >&2
      exit 2
      ;;
  esac
  shift
done

"$ROOT/scripts/init.sh"
"$ROOT/scripts/doctor.sh" --require-config
load_env

load_release_state
old_tag="${CURRENT_TAG:-}"
old_previous_tag="${PREVIOUS_TAG:-}"
old_rollback_backup="${ROLLBACK_BACKUP:-}"

if [[ -n "$requested_tag" ]]; then
  desired_tag="$requested_tag"
elif [[ -n "${SYNDICATOR_IMAGE_TAG:-}" ]]; then
  desired_tag="$SYNDICATOR_IMAGE_TAG"
else
  desired_tag="$(git rev-parse --short=12 HEAD 2>/dev/null || printf 'local')"
fi
if [[ ! "$desired_tag" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
  echo "Invalid image tag: $desired_tag" >&2
  exit 1
fi

backup_path=""
existing_container="$(compose ps -a -q n8n)"
if [[ "$backup_on_change" -eq 1 && \
      ( -n "$existing_container" ) && \
      ( -z "$old_tag" || "$old_tag" != "$desired_tag" ) ]]; then
  backup_dir="$(resolve_from_root "${SYNDICATOR_BACKUP_DIR:-backups}")"
  from_tag="${old_tag:-legacy}"
  backup_path="$backup_dir/pre-update-${from_tag}-to-${desired_tag}-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  "$ROOT/scripts/backup.sh" --output "$backup_path"
fi

export SYNDICATOR_IMAGE_TAG="$desired_tag"
if [[ "$pull" -eq 1 ]]; then
  compose build --pull
else
  compose build
fi
compose up -d --remove-orphans
"$ROOT/scripts/bootstrap-n8n.sh"
"$ROOT/scripts/verify.sh"

if [[ "$old_tag" != "$desired_tag" ]]; then
  previous_tag="$old_tag"
  rollback_backup="$backup_path"
else
  previous_tag="$old_previous_tag"
  rollback_backup="$old_rollback_backup"
fi

write_release_state "$desired_tag" "$previous_tag" "$rollback_backup"

echo "Deployment $desired_tag is healthy."
