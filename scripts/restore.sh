#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

forced_image_tag="${SYNDICATOR_IMAGE_TAG:-}"
confirmed=0
build_images=1
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --yes)
      confirmed=1
      ;;
    --no-build)
      build_images=0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done
if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 --yes [--no-build] ARCHIVE.tar.gz" >&2
  exit 2
fi
if [[ "$confirmed" -ne 1 ]]; then
  echo "Restore replaces current configuration and persistent data; pass --yes." >&2
  exit 2
fi

archive="$(resolve_from_root "$1")"
if [[ ! -f "$archive" ]]; then
  echo "Backup archive not found: $archive" >&2
  exit 1
fi

for command in docker python3 tar; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 1
  fi
done
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is unavailable." >&2
  exit 1
fi

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
python3 - "$archive" "$staging" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2]).resolve()
with tarfile.open(archive, "r:gz") as bundle:
    for member in bundle.getmembers():
        target = (destination / member.name).resolve()
        if destination != target and destination not in target.parents:
            raise SystemExit(f"Unsafe archive member: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise SystemExit(f"Unsupported archive member: {member.name}")
    bundle.extractall(destination)

manifest_path = destination / "manifest.json"
if not manifest_path.is_file():
    raise SystemExit("Backup has no manifest.json")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("format_version") != 1:
    raise SystemExit(f"Unsupported backup format: {manifest.get('format_version')}")
for relative, expected in manifest.get("files", {}).items():
    path = destination / relative
    if not path.is_file():
        raise SystemExit(f"Backup member is missing: {relative}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Checksum mismatch: {relative}")
PY

for volume in n8n_data sftp_data sftp_host_keys; do
  if [[ ! -f "$staging/volumes/${volume}.tar.gz" ]]; then
    echo "Backup is missing volume archive: $volume" >&2
    exit 1
  fi
done
if [[ ! -f "$staging/config/environment.env" ]]; then
  echo "Backup is missing environment configuration." >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  load_env
  compose stop n8n sftp pyautoflip >/dev/null 2>&1 || true
fi

mkdir -p "$(dirname "$ENV_FILE")"
cp "$staging/config/environment.env" "$ENV_FILE"
chmod 600 "$ENV_FILE"
unset SYNDICATOR_IMAGE_TAG
load_env
environment_image_tag="${SYNDICATOR_IMAGE_TAG:-}"

restore_file() {
  local name="$1"
  local target="$2"
  mkdir -p "$(dirname "$target")"
  if [[ -f "$staging/config/$name" ]]; then
    cp "$staging/config/$name" "$target"
    chmod 600 "$target"
  else
    rm -f "$target"
  fi
}

owner_env="$(resolve_from_root "${N8N_OWNER_ENV_FILE:-secrets/n8n_owner.env}")"
api_key="$(resolve_from_root "${N8N_API_KEY_FILE:-secrets/n8n_api_key}")"
bootstrap_state="$(resolve_from_root "${N8N_BOOTSTRAP_STATE_FILE:-secrets/bootstrap.sha256}")"
private_key="$(resolve_from_root "${SFTP_PRIVATE_KEY_FILE:-secrets/sftp_n8n_ed25519}")"
keys_dir="$(resolve_from_root "${SFTP_KEYS_DIR:-sftp/keys}")"
release_state="$(release_state_file)"

restore_file n8n_owner.env "$owner_env"
restore_file n8n_api_key "$api_key"
restore_file bootstrap.sha256 "$bootstrap_state"
restore_file sftp_private_key "$private_key"
restore_file release.env "$release_state"
if [[ ! -s "$owner_env" || ! -s "$private_key" ]]; then
  echo "Backup is missing required owner or SFTP credentials." >&2
  exit 1
fi

rm -rf "$keys_dir"
mkdir -p "$(dirname "$keys_dir")"
if [[ -d "$staging/config/sftp_keys" ]]; then
  cp -Rp "$staging/config/sftp_keys" "$keys_dir"
else
  mkdir -p "$keys_dir"
  ssh-keygen -y -f "$private_key" >"$keys_dir/n8n.pub"
fi

if [[ -n "$forced_image_tag" ]]; then
  export SYNDICATOR_IMAGE_TAG="$forced_image_tag"
elif [[ -n "$environment_image_tag" ]]; then
  export SYNDICATOR_IMAGE_TAG="$environment_image_tag"
else
  unset SYNDICATOR_IMAGE_TAG CURRENT_TAG PREVIOUS_TAG ROLLBACK_BACKUP
  load_release_state
  if [[ -n "${CURRENT_TAG:-}" ]]; then
    export SYNDICATOR_IMAGE_TAG="$CURRENT_TAG"
  fi
fi

for volume in n8n_data sftp_data sftp_host_keys; do
  echo "Restoring volume $volume..."
  # shellcheck disable=SC2016
  compose run --rm --no-deps --user root \
    -e "RESTORE_VOLUME=$volume" \
    -v "$staging/volumes:/backup:ro" \
    --entrypoint sh volume-tool -c '
      target="/volumes/${RESTORE_VOLUME}" &&
      rm -rf "$target"/* "$target"/.[!.]* "$target"/..?* &&
      tar -xzf "/backup/${RESTORE_VOLUME}.tar.gz" -C "$target"
    ' >/dev/null
done

if [[ "$build_images" -eq 1 ]]; then
  compose build
fi
compose up -d --remove-orphans
"$ROOT/scripts/bootstrap-n8n.sh"
"$ROOT/scripts/verify.sh"
echo "Restore from $archive completed."
