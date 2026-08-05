FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin pyautoflip

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && chown -R pyautoflip:pyautoflip /app

# Match n8n's node user (uid 1000). Compose may also set user: "1000:1000";
# without HOME, InsightFace tries /.insightface and fails with EACCES.
ENV HOME=/home/pyautoflip
ENV PYAUTOFLIP_FILES_ROOT=/files

USER pyautoflip

# Warm InsightFace packs into $HOME/.insightface so runtime needs no downloads.
# pyautoflip's face detector defaults to buffalo_s (not buffalo_l).
RUN python - <<'PY'
from insightface.app import FaceAnalysis
for name in ("buffalo_s", "buffalo_l"):
    app = FaceAnalysis(name=name, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    print(f"insightface {name} ready")
PY

COPY --chown=pyautoflip:pyautoflip app.py .

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "900"]
