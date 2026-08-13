#!/bin/sh
# Sync locked community nodes into the n8n data volume, then start n8n.
set -eu

NODES_DIR="/home/node/.n8n/nodes"
SEED_DIR="/opt/n8n-nodes-seed"
SEED_MARKER="$NODES_DIR/.syndicator-seed.sha256"

if [ -d "$SEED_DIR/node_modules" ] && [ -f "$SEED_DIR/package-lock.json" ]; then
	seed_hash="$(sha256sum "$SEED_DIR/package-lock.json" | cut -d ' ' -f 1)"
	installed_hash="$(cat "$SEED_MARKER" 2>/dev/null || true)"
	if [ "$seed_hash" != "$installed_hash" ]; then
		echo "Syncing locked community nodes into $NODES_DIR"
		rm -rf "$NODES_DIR"
		mkdir -p "$NODES_DIR"
		cp -a "$SEED_DIR"/. "$NODES_DIR"/
		printf '%s\n' "$seed_hash" >"$SEED_MARKER"
	fi
fi

if [ -z "${N8N_INSTANCE_OWNER_PASSWORD_HASH:-}" ]; then
	if [ -z "${N8N_OWNER_PASSWORD:-}" ]; then
		echo "N8N_OWNER_PASSWORD is required" >&2
		exit 1
	fi
	N8N_INSTANCE_OWNER_PASSWORD_HASH="$(node /opt/syndicator/hash-owner-password.js)"
	export N8N_INSTANCE_OWNER_PASSWORD_HASH
	unset N8N_OWNER_PASSWORD
fi

# Preserve upstream custom-certificate handling + default start.
exec /docker-entrypoint.sh "$@"
