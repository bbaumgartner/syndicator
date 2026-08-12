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
restore_mutated=0
restore_pending=""
cleanup() {
  status=$?
  if [[ "$status" -ne 0 && "$restore_mutated" -eq 1 ]]; then
    compose stop n8n pyautoflip sftp >/dev/null 2>&1 || true
    if [[ -n "$restore_pending" ]]; then
      mkdir -p "$(dirname "$restore_pending")"
      {
        printf 'RESTORE_ARCHIVE=%q\n' "$archive"
        printf 'RESTORE_SOURCE_REVISION=%q\n' "${source_revision:-unknown}"
        printf 'RESTORE_IMAGE_TAG=%q\n' "${desired_image_tag:-unknown}"
      } >"$restore_pending"
      chmod 600 "$restore_pending"
    fi
    echo "Restore failed after mutation; unverified services were stopped." >&2
  fi
  rm -rf "$staging"
  exit "$status"
}
trap cleanup EXIT
python3 - "$archive" "$staging" <<'PY'
import hashlib
import json
import posixpath
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

for name in ("n8n_data", "sftp_data", "sftp_host_keys"):
    volume_archive = destination / "volumes" / f"{name}.tar.gz"
    try:
        volume = tarfile.open(volume_archive, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise SystemExit(f"Invalid volume archive {name}: {exc}") from exc
    with volume:
        for member in volume.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise SystemExit(f"Unsafe {name} member: {member.name}")
            if member.isdev():
                raise SystemExit(f"Unsupported {name} member: {member.name}")
            if member.issym() or member.islnk():
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(member.name), member.linkname)
                )
                if member.linkname.startswith("/") or resolved == ".." or resolved.startswith("../"):
                    raise SystemExit(f"Unsafe {name} link: {member.name}")
PY

for required in \
  volumes/n8n_data.tar.gz \
  volumes/sftp_data.tar.gz \
  volumes/sftp_host_keys.tar.gz \
  config/environment.env \
  config/n8n_owner.env \
  config/sftp_private_key; do
  if [[ ! -f "$staging/$required" ]]; then
    echo "Backup is missing required member: $required" >&2
    exit 1
  fi
done

manifest_revision="$(python3 - "$staging/manifest.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle).get("git_revision", "unknown"))
PY
)"
source_revision="${SYNDICATOR_SOURCE_REVISION:-}"
if [[ -z "$source_revision" ]]; then
  source_revision="$(git -C "$SOURCE_ROOT" rev-parse HEAD 2>/dev/null || printf 'unknown')"
fi
if [[ "$manifest_revision" != "unknown" && \
      "$source_revision" != "$manifest_revision" ]]; then
  echo "Backup requires Git revision $manifest_revision." >&2
  echo "Selected source is $source_revision; refusing a mixed-version restore." >&2
  exit 1
fi
if [[ "${SYNDICATOR_ALLOW_DIRTY:-0}" != "1" ]] && \
   [[ -e "$SOURCE_ROOT/.git" ]] && \
   [[ -n "$(git -C "$SOURCE_ROOT" status --porcelain)" ]]; then
  echo "Refusing to restore with a dirty source checkout." >&2
  exit 1
fi

target_env_file="$ENV_FILE"
ENV_FILE="$staging/config/environment.env"
load_env
restore_pending="$(release_state_file).restore-pending"
environment_image_tag="${SYNDICATOR_IMAGE_TAG:-}"
archive_release_tag=""
if [[ -f "$staging/config/release.env" ]]; then
  archive_release_tag="$(python3 - "$ROOT/scripts" "$staging/config/release.env" <<'PY'
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from dotenv import parse

print(dict(parse(Path(sys.argv[2]))).get("CURRENT_TAG", ""))
PY
)"
fi
if [[ -n "$forced_image_tag" ]]; then
  desired_image_tag="$forced_image_tag"
elif [[ -n "$environment_image_tag" ]]; then
  desired_image_tag="$environment_image_tag"
elif [[ -n "$archive_release_tag" ]]; then
  desired_image_tag="$archive_release_tag"
else
  desired_image_tag="$(git rev-parse --short=12 HEAD 2>/dev/null || printf 'local')"
fi
if [[ ! "$desired_image_tag" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
  echo "Backup selected an invalid image tag: $desired_image_tag" >&2
  exit 1
fi

if [[ ! -d "$staging/config/sftp_keys" ]]; then
  mkdir -p "$staging/config/sftp_keys"
  ssh-keygen -y -f "$staging/config/sftp_private_key" \
    >"$staging/config/sftp_keys/n8n.pub"
fi
export N8N_OWNER_ENV_FILE="$staging/config/n8n_owner.env"
export SFTP_KEYS_DIR="$staging/config/sftp_keys"
export SYNDICATOR_IMAGE_TAG="$desired_image_tag"

if [[ "$build_images" -eq 1 ]]; then
  compose build
else
  for image in \
    "syndicator-n8n:$desired_image_tag" \
    "syndicator-pyautoflip:$desired_image_tag"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      echo "Required restore image is missing: $image" >&2
      exit 1
    fi
  done
fi

compose stop n8n sftp pyautoflip >/dev/null
for service in n8n sftp pyautoflip; do
  if [[ -n "$(compose ps --status running -q "$service")" ]]; then
    echo "Service did not stop before restore: $service" >&2
    exit 1
  fi
done

mkdir -p "$(dirname "$restore_pending")"
umask 077
{
  printf 'RESTORE_ARCHIVE=%q\n' "$archive"
  printf 'RESTORE_SOURCE_REVISION=%q\n' "$source_revision"
  printf 'RESTORE_IMAGE_TAG=%q\n' "$desired_image_tag"
} >"$restore_pending"
chmod 600 "$restore_pending"
restore_mutated=1

ENV_FILE="$target_env_file"
mkdir -p "$(dirname "$ENV_FILE")"
cp "$staging/config/environment.env" "$ENV_FILE"
chmod 600 "$ENV_FILE"
unset N8N_OWNER_ENV_FILE SFTP_KEYS_DIR SYNDICATOR_IMAGE_TAG
load_env

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
fi

export N8N_OWNER_ENV_FILE="$owner_env"
export SFTP_KEYS_DIR="$keys_dir"
export SYNDICATOR_IMAGE_TAG="$desired_image_tag"

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

compose up -d --remove-orphans
if [[ "${SYNDICATOR_TEST_FAIL_RESTORE_AFTER_START:-0}" == "1" ]]; then
  echo "Deliberate post-start restore failure requested by integration test." >&2
  false
fi
"$ROOT/scripts/bootstrap-n8n.sh"
"$ROOT/scripts/verify.sh"

unset CURRENT_TAG PREVIOUS_TAG ROLLBACK_BACKUP \
  CURRENT_GIT_REVISION PREVIOUS_GIT_REVISION \
  CURRENT_SOURCE_ROOT PREVIOUS_SOURCE_ROOT
load_release_state
restored_current_tag="${CURRENT_TAG:-$desired_image_tag}"
restored_current_revision="${CURRENT_GIT_REVISION:-$manifest_revision}"
restored_previous_tag="${PREVIOUS_TAG:-}"
restored_previous_revision="${PREVIOUS_GIT_REVISION:-}"
restored_rollback_backup="${ROLLBACK_BACKUP:-}"
restored_current_source="$(materialize_release_source "$restored_current_revision")"
restored_previous_source=""
if [[ -n "$restored_previous_revision" ]] && \
   git cat-file -e "${restored_previous_revision}^{commit}" 2>/dev/null; then
  restored_previous_source="$(materialize_release_source "$restored_previous_revision")"
fi
write_release_state \
  "$restored_current_tag" \
  "$restored_previous_tag" \
  "$restored_rollback_backup" \
  "$restored_current_revision" \
  "$restored_previous_revision" \
  "$restored_current_source" \
  "$restored_previous_source"
rm -f "$restore_pending"
restore_mutated=0
echo "Restore from $archive completed."
