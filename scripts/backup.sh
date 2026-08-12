#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

load_env
"$ROOT/scripts/doctor.sh" --require-config >/dev/null

output=""
if [[ "${1:-}" == "--output" && -n "${2:-}" && "$#" -eq 2 ]]; then
  output="$2"
elif [[ "$#" -ne 0 ]]; then
  echo "Usage: $0 [--output ARCHIVE.tar.gz]" >&2
  exit 2
fi

backup_dir="$(resolve_from_root "${SYNDICATOR_BACKUP_DIR:-backups}")"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
if [[ -z "$output" ]]; then
  output="$backup_dir/syndicator-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
else
  output="$(resolve_from_root "$output")"
  mkdir -p "$(dirname "$output")"
fi
if [[ -e "$output" ]]; then
  echo "Backup already exists: $output" >&2
  exit 1
fi

staging="$(mktemp -d "$backup_dir/.syndicator-backup.XXXXXX")"
mkdir -p "$staging/config" "$staging/volumes"
chmod 700 "$staging" "$staging/config" "$staging/volumes"
temporary_output=""

running_services=()
for service in sftp pyautoflip n8n; do
  if [[ -n "$(compose ps --status running -q "$service")" ]]; then
    running_services+=("$service")
  fi
done
restarted=0

restart_services() {
  if [[ "$restarted" -eq 0 && "${#running_services[@]}" -gt 0 ]]; then
    compose start "${running_services[@]}" >/dev/null
    restarted=1
  fi
}

cleanup() {
  status=$?
  restart_services || true
  rm -rf "$staging"
  if [[ -n "$temporary_output" ]]; then
    rm -f "$temporary_output"
  fi
  exit "$status"
}
trap cleanup EXIT

if [[ "${#running_services[@]}" -gt 0 ]]; then
  echo "Stopping stateful services for a consistent backup..."
  compose stop "${running_services[@]}" >/dev/null
fi

host_uid="$(id -u)"
host_gid="$(id -g)"
for volume in n8n_data sftp_data sftp_host_keys; do
  echo "Archiving volume $volume..."
  # shellcheck disable=SC2016
  compose run --rm --no-deps --user root \
    -e "BACKUP_VOLUME=$volume" \
    -e "HOST_UID=$host_uid" \
    -e "HOST_GID=$host_gid" \
    -v "$staging/volumes:/backup" \
    --entrypoint sh volume-tool -c '
      tar -czf "/backup/${BACKUP_VOLUME}.tar.gz" \
        -C "/volumes/${BACKUP_VOLUME}" . &&
      chown "${HOST_UID}:${HOST_GID}" "/backup/${BACKUP_VOLUME}.tar.gz"
    ' >/dev/null
done

copy_file() {
  local source="$1"
  local name="$2"
  if [[ -f "$source" ]]; then
    cp -p "$source" "$staging/config/$name"
  fi
}

owner_env="$(resolve_from_root "${N8N_OWNER_ENV_FILE:-secrets/n8n_owner.env}")"
api_key="$(resolve_from_root "${N8N_API_KEY_FILE:-secrets/n8n_api_key}")"
bootstrap_state="$(resolve_from_root "${N8N_BOOTSTRAP_STATE_FILE:-secrets/bootstrap.sha256}")"
private_key="$(resolve_from_root "${SFTP_PRIVATE_KEY_FILE:-secrets/sftp_n8n_ed25519}")"
keys_dir="$(resolve_from_root "${SFTP_KEYS_DIR:-sftp/keys}")"
release_state="$(release_state_file)"

copy_file "$ENV_FILE" environment.env
copy_file "$owner_env" n8n_owner.env
copy_file "$api_key" n8n_api_key
copy_file "$bootstrap_state" bootstrap.sha256
copy_file "$private_key" sftp_private_key
copy_file "$release_state" release.env
if [[ -d "$keys_dir" ]]; then
  cp -Rp "$keys_dir" "$staging/config/sftp_keys"
fi

git_revision="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
GIT_REVISION="$git_revision" python3 - "$staging" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
files = {}
for path in sorted(root.rglob("*")):
    if path.is_file() and path.name != "manifest.json":
        files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {
    "format_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "git_revision": os.environ["GIT_REVISION"],
    "files": files,
}
(root / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

temporary_output="${output}.tmp.$$"
tar -czf "$temporary_output" -C "$staging" .
chmod 600 "$temporary_output"
mv "$temporary_output" "$output"
temporary_output=""

restart_services
if [[ " ${running_services[*]} " == *" n8n "* && \
      " ${running_services[*]} " == *" sftp "* && \
      " ${running_services[*]} " == *" pyautoflip "* ]]; then
  "$ROOT/scripts/verify.sh" >/dev/null
fi

echo "Backup written to $output"
