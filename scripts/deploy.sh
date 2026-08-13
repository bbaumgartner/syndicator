#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

pull=0
requested_tag=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pull)
      pull=1
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
      echo "Usage: $0 [--pull] [--tag TAG]" >&2
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
old_revision="${CURRENT_GIT_REVISION:-}"
desired_revision="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"

if [[ "${SYNDICATOR_ALLOW_DIRTY:-0}" != "1" ]] && \
   [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  echo "Refusing to build a release from a dirty working tree." >&2
  exit 1
fi

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
if [[ -n "$old_tag" && "$old_tag" == "$desired_tag" && \
      -n "$old_revision" && "$old_revision" != "$desired_revision" ]]; then
  echo "Image tag $desired_tag already belongs to Git revision $old_revision." >&2
  echo "Use a new --tag value for revision $desired_revision." >&2
  exit 1
fi

export SYNDICATOR_IMAGE_TAG="$desired_tag"
if [[ "$pull" -eq 1 ]]; then
  compose build --pull
else
  compose build
fi

pending_state="$(pending_release_file)"
mkdir -p "$(dirname "$pending_state")"
umask 077
{
  printf 'PENDING_TAG=%q\n' "$desired_tag"
  printf 'PENDING_GIT_REVISION=%q\n' "$desired_revision"
} >"$pending_state"
chmod 600 "$pending_state"

runtime_mutated=0
deployment_cleanup() {
  status=$?
  if [[ "$status" -ne 0 && "$runtime_mutated" -eq 1 ]]; then
    if compose stop n8n pyautoflip sftp >/dev/null 2>&1; then
      echo "Deployment failed; the unverified services were stopped." >&2
    else
      echo "Deployment failed and automatic service shutdown also failed." >&2
    fi
    for service in n8n pyautoflip sftp; do
      if [[ -n "$(compose ps --status running -q "$service" 2>/dev/null || true)" ]]; then
        echo "Unverified service is still running: $service" >&2
      fi
    done
  fi
  exit "$status"
}
trap deployment_cleanup EXIT

runtime_mutated=1
compose up -d --remove-orphans
if [[ "${SYNDICATOR_TEST_FAIL_AFTER_START:-0}" == "1" ]]; then
  echo "Deliberate post-start failure requested by integration test." >&2
  false
fi
wait_for_n8n
run_reconcile
"$ROOT/scripts/verify.sh"

write_release_state "$desired_tag" "$desired_revision"
rm -f "$pending_state"
trap - EXIT

echo "Deployment $desired_tag is healthy."
