#!/bin/bash
# Wrapper around atmoz/sftp entrypoint:
# - durable host keys in a named volume (no host-side keygen / bind mounts)
# - real sshd is replaced at build time by sshd-wrapper (see Dockerfile)
set -Eeo pipefail

HOST_KEY_DIR="${SFTP_HOST_KEY_DIR:-/etc/ssh/host_keys}"

mkdir -p "$HOST_KEY_DIR"

if [[ ! -f "$HOST_KEY_DIR/ssh_host_ed25519_key" ]]; then
  echo "[syndicator-sftp] generating ed25519 host key"
  ssh-keygen -t ed25519 -f "$HOST_KEY_DIR/ssh_host_ed25519_key" -N '' </dev/null
fi
if [[ ! -f "$HOST_KEY_DIR/ssh_host_rsa_key" ]]; then
  echo "[syndicator-sftp] generating rsa host key"
  ssh-keygen -t rsa -b 4096 -f "$HOST_KEY_DIR/ssh_host_rsa_key" -N '' </dev/null
fi

chmod 600 "$HOST_KEY_DIR"/ssh_host_*_key
chmod 644 "$HOST_KEY_DIR"/ssh_host_*.pub 2>/dev/null || true
# Install into /etc/ssh (image layer is writable; avoids 4 bind mounts).
cp -a "$HOST_KEY_DIR"/ssh_host_ed25519_key "$HOST_KEY_DIR"/ssh_host_ed25519_key.pub \
  "$HOST_KEY_DIR"/ssh_host_rsa_key "$HOST_KEY_DIR"/ssh_host_rsa_key.pub \
  /etc/ssh/
chmod 600 /etc/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_rsa_key

exec /entrypoint "$@"
