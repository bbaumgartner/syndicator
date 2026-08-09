# pyautoflip sidecar

HTTP wrapper around [pyautoflip](https://github.com/AhmedHisham1/pyautoflip) (saliency mode)
for the n8n **Adapt Reel Media** workflow. Runs in its own container next to n8n and shares
the `/files` volume so videos are not uploaded through HTTP bodies.

Defined as the `pyautoflip` service in [`../docker-compose.yml`](../docker-compose.yml).

```bash
docker compose up -d --build pyautoflip
```

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
- Force **narrow** (exact-AR) crop width — skip upstream’s +30% wide crop that letterboxes.
- Replace stretch-and-darken padding with **full-bleed center-crop** (no black bars, no aspect stretch).
- Encode with **libx264 from raw frames** (no OpenCV `mp4v` intermediate); mux audio in a second `-c:v copy` step so A/V stays in sync.

Encode knobs (optional env on the `pyautoflip` service):

| Env | Default | Meaning |
| --- | --- | --- |
| `PYAUTOFLIP_CRF` | `18` | libx264 CRF (lower = higher quality) |
| `PYAUTOFLIP_PRESET` | `medium` | libx264 preset |

## Local image build

```bash
cd pyautoflip
docker build -t syndicator-pyautoflip:local .
```

## n8n workflow

**Adapt Reel Media** (`y9TTx7N8Iygn88ry`) calls this service. The live workflow
is edited in n8n; export back with `scripts/export-workflows.sh`.

After cutover, remove any ad-hoc `pyautoflip` service from host `~/n8n/compose.yaml`
so `docker-compose.yml` is the single definition.
