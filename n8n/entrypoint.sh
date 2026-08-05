#!/bin/sh
# Seed community nodes into the n8n data volume when missing, then start n8n.
set -eu

NODES_DIR="/home/node/.n8n/nodes"
SEED_DIR="/opt/n8n-nodes-seed"

if [ -d "$SEED_DIR/node_modules" ]; then
	mkdir -p "$NODES_DIR"
	if [ ! -f "$NODES_DIR/package.json" ]; then
		echo "Seeding community nodes into $NODES_DIR"
		cp -a "$SEED_DIR"/. "$NODES_DIR"/
	fi
fi

# Preserve upstream custom-certificate handling + default start.
exec /docker-entrypoint.sh "$@"
