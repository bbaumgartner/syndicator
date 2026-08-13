#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

pull=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pull)
      pull=1
      ;;
    *)
      echo "Usage: $0 [--pull]" >&2
      exit 2
      ;;
  esac
  shift
done

"$ROOT/scripts/init.sh"
"$ROOT/scripts/doctor.sh" --require-config

if [[ -z "${SYNDICATOR_IMAGE_TAG:-}" ]]; then
  SYNDICATOR_IMAGE_TAG="$(git rev-parse --short=12 HEAD 2>/dev/null || printf 'local')"
  export SYNDICATOR_IMAGE_TAG
fi

if [[ "$pull" -eq 1 ]]; then
  compose build --pull
else
  compose build
fi

runtime_mutated=0
deployment_cleanup() {
  status=$?
  if [[ "$status" -ne 0 && "$runtime_mutated" -eq 1 ]]; then
    if compose stop n8n pyautoflip sftp >/dev/null 2>&1; then
      echo "Deployment failed; the unverified services were stopped." >&2
    else
      echo "Deployment failed and automatic service shutdown also failed." >&2
    fi
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

trap - EXIT
echo "Deployment ${SYNDICATOR_IMAGE_TAG} is healthy."
