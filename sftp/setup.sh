#!/bin/bash
# Supported atmoz/sftp startup hook: persist host keys, refresh user keys, and
# repair the writable data directory without replacing the sshd binary.
set -Eeo pipefail

HOST_KEY_DIR="${SFTP_HOST_KEY_DIR:-/etc/ssh/host_keys}"
SFTP_UID="${SFTP_UID:-1001}"
SFTP_GID="${SFTP_GID:-100}"
DATA_DIR="${SFTP_DATA_DIR:-/home/sftp/syndicator}"

mkdir -p "$HOST_KEY_DIR"

if [[ ! -f "$HOST_KEY_DIR/ssh_host_ed25519_key" ]]; then
  ssh-keygen -t ed25519 -f "$HOST_KEY_DIR/ssh_host_ed25519_key" -N '' </dev/null
fi
if [[ ! -f "$HOST_KEY_DIR/ssh_host_rsa_key" ]]; then
  ssh-keygen -t rsa -b 4096 -f "$HOST_KEY_DIR/ssh_host_rsa_key" -N '' </dev/null
fi

install -m 600 "$HOST_KEY_DIR/ssh_host_ed25519_key" /etc/ssh/ssh_host_ed25519_key
install -m 600 "$HOST_KEY_DIR/ssh_host_rsa_key" /etc/ssh/ssh_host_rsa_key
install -m 644 "$HOST_KEY_DIR/ssh_host_ed25519_key.pub" /etc/ssh/ssh_host_ed25519_key.pub
install -m 644 "$HOST_KEY_DIR/ssh_host_rsa_key.pub" /etc/ssh/ssh_host_rsa_key.pub

for home in /home/*; do
  [[ -d "$home" ]] || continue
  user="$(basename "$home")"
  keys_dir="$home/.ssh/keys"
  [[ -d "$keys_dir" ]] || continue
  auth="$home/.ssh/authorized_keys"
  if compgen -G "$keys_dir/*" >/dev/null; then
    sort -u "$keys_dir"/* >"$auth"
  else
    : >"$auth"
  fi
  chown "$(id -u "$user")" "$auth"
  chmod 600 "$auth"
done

if [[ -d "$DATA_DIR" ]]; then
  chown "${SFTP_UID}:${SFTP_GID}" "$DATA_DIR"
  chmod 755 "$DATA_DIR"
fi
