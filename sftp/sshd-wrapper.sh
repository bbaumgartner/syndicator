#!/bin/bash
# Installed as /usr/sbin/sshd (real binary at /usr/sbin/sshd.real).
# atmoz entrypoint execs /usr/sbin/sshd — refresh keys + ownership first.
set -Eeo pipefail

SFTP_UID="${SFTP_UID:-1001}"
SFTP_GID="${SFTP_GID:-100}"
DATA_DIR="${SFTP_DATA_DIR:-/home/sftp/syndicator}"

for home in /home/*; do
  [[ -d "$home" ]] || continue
  user="$(basename "$home")"
  keys_dir="$home/.ssh/keys"
  [[ -d "$keys_dir" ]] || continue
  auth="$home/.ssh/authorized_keys"
  mkdir -p "$home/.ssh"
  if compgen -G "$keys_dir/*" >/dev/null; then
    cat "$keys_dir"/* | sort -u >"$auth"
  else
    : >"$auth"
  fi
  uid="$(id -u "$user" 2>/dev/null || echo "$SFTP_UID")"
  chown "$uid" "$auth"
  chmod 600 "$auth"
done

if [[ -d "$DATA_DIR" ]]; then
  chown -R "${SFTP_UID}:${SFTP_GID}" "$DATA_DIR"
  chmod 755 "$DATA_DIR"
fi

exec /usr/sbin/sshd.real "$@"
