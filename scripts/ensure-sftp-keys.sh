#!/usr/bin/env bash
# Idempotently create the n8n↔sftp client keypair and authorized public key.
# Safe to run before every `docker compose up`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

if [[ -f "$ENV_FILE" ]]; then
  load_env
fi

key_file="${SFTP_PRIVATE_KEY_FILE:-./secrets/sftp_n8n_ed25519}"
key_file="$(resolve_from_root "$key_file")"
keys_dir="$(resolve_from_root "${SFTP_KEYS_DIR:-sftp/keys}")"
pub_file="$keys_dir/n8n.pub"

mkdir -p "$(dirname "$key_file")" "$keys_dir"

if [[ ! -f "$key_file" ]]; then
  echo "Generating SFTP client key: $key_file"
  ssh-keygen -t ed25519 -f "$key_file" -N '' -C 'syndicator-n8n' </dev/null
  chmod 600 "$key_file"
else
  echo "SFTP client key already present: $key_file"
fi

ssh-keygen -y -f "$key_file" >"$pub_file"
chmod 644 "$pub_file"
# ssh-keygen also writes key_file.pub on create; keep a single canonical pubkey path.
rm -f "${key_file}.pub"
echo "Authorized public key: $pub_file"
