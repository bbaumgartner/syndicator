# pyautoflip sidecar

HTTP wrapper around [pyautoflip](https://github.com/AhmedHisham1/pyautoflip) (saliency mode)
for the n8n **Adapt Reel Media** workflow. Runs in its own container next to n8n and shares
the `/files` volume so videos are not uploaded through HTTP bodies.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness |
| `POST` | `/reframe` | Reframe a video on the shared volume |

```json
POST /reframe
{
  "input_path": "/files/syndicator-source-….mp4",
  "output_path": "/files/syndicator-video-…-9x16.mp4",
  "aspect_ratio": "9:16",
  "method": "saliency",
  "motion_threshold": 0.5,
  "padding_method": "blur"
}
```

Paths must stay under `/files` (or `$PYAUTOFLIP_FILES_ROOT`). Response includes
`output_path`, `width`, `height`, and `duration_ms`.

**Upstream workarounds** (applied in `app.py` before `reframe_video`):

- Disable saliency split-screen (`needs_split_screen` → always false); keep single-crop. [pyautoflip issue](https://github.com/AhmedHisham1/pyautoflip/issues/7)
- Fix `"4:5"` mapping (`_aspect_ratio_to_tuple` would otherwise fall back to **3:4**) [pyautoflip issue](https://github.com/AhmedHisham1/pyautoflip/issues/6).

## Wire into the n8n host

1. Check out syndicator on the host (same machine as n8n), e.g. `~/git/syndicator`.
2. Add a `pyautoflip` service to the **existing** n8n `compose.yaml` (do not invent a
   `./services/pyautoflip` under `~/n8n` — that path only exists in the syndicator repo).
3. Point `build:` at the syndicator sidecar directory.
4. Mount the **same** host path n8n uses for `/files` (on bellair that is
   `./local-files:/files`, not `n8n_data` — `n8n_data` is only for `/home/node/.n8n`).

Example for `~/n8n/compose.yaml` when syndicator lives at `~/git/syndicator`:

```yaml
services:
  pyautoflip:
    build: ../git/syndicator/services/pyautoflip
    restart: unless-stopped
    volumes:
      - ./local-files:/files
    expose:
      - "8080"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 120s

  n8n:
    # ... existing config ...
    volumes:
      - n8n_data:/home/node/.n8n
      - ./local-files:/files
```

Same compose project → default network → n8n reaches `http://pyautoflip:8080`.

5. Recreate:

```bash
cd ~/n8n
docker compose up -d --build pyautoflip
```

(Optional buildx warning is harmless if the image still builds.)

6. From inside the n8n container:

```bash
docker compose exec n8n wget -qO- http://pyautoflip:8080/health
# {"status":"ok"}
```

7. Smoke-test (paths must exist under `./local-files` on the host = `/files` in both containers):

```bash
docker compose exec n8n wget -qO- --post-data='{"input_path":"/files/sample.mp4","output_path":"/files/sample-9x16.mp4","aspect_ratio":"9:16","method":"saliency"}' --header='Content-Type: application/json' http://pyautoflip:8080/reframe
```

## Local image build

```bash
cd services/pyautoflip
docker build -t syndicator-pyautoflip:local .
```

## n8n workflow

**Adapt Reel Media** (`y9TTx7N8Iygn88ry`) calls this service. A validated SDK
reference of the graph lives in [`adapt-reel-media.workflow.js`](adapt-reel-media.workflow.js)
(for rebuilds; the live workflow is edited in n8n).
