#!/usr/bin/env bash
set -euo pipefail

image="${1:-syndicator-pyautoflip:local}"
tmp="$(mktemp -d)"
name="syndicator-reframe-${RANDOM}"
port="$(python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
chmod 777 "$tmp"

cleanup() {
  status=$?
  if [[ "$status" -ne 0 ]]; then
    docker logs "$name" >&2 || true
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$tmp"
  exit "$status"
}
trap cleanup EXIT

docker run -d --rm \
  --name "$name" \
  -p "127.0.0.1:${port}:8080" \
  -v "$tmp:/files" \
  "$image" >/dev/null

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${port}/health" >/dev/null

docker exec "$name" ffmpeg \
  -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc=size=320x180:rate=5" \
  -t 1 -pix_fmt yuv420p /files/input.mp4

curl -fsS --max-time 180 \
  -H 'Content-Type: application/json' \
  -d '{
    "input_path": "/files/input.mp4",
    "output_path": "/files/output.mp4",
    "aspect_ratio": "9:16",
    "method": "saliency"
  }' \
  "http://127.0.0.1:${port}/reframe" >"$tmp/response.json"

python3 - "$tmp/response.json" "$tmp/output.mp4" <<'PY'
import json
from pathlib import Path
import sys

response = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
output = Path(sys.argv[2])
if not output.is_file() or output.stat().st_size == 0:
    raise SystemExit("reframe did not create output media")
if response.get("width", 0) <= 0 or response.get("height", 0) <= 0:
    raise SystemExit(f"invalid reframe dimensions: {response}")
PY

echo "Production pyautoflip reframe smoke test passed."
