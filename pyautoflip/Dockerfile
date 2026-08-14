FROM python:3.14-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52

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
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && chown -R pyautoflip:pyautoflip /app

# Match n8n's node user (uid 1000). Compose may also set user: "1000:1000";
# without HOME, InsightFace tries /.insightface and fails with EACCES.
ENV HOME=/home/pyautoflip
ENV PYAUTOFLIP_FILES_ROOT=/files

USER pyautoflip

# Install and verify pyautoflip's buffalo_s model in the immutable image.
ARG PYAUTOFLIP_WARM_MODELS=1
COPY --chown=pyautoflip:pyautoflip warm_models.py .
RUN if [ "$PYAUTOFLIP_WARM_MODELS" = "1" ]; then \
      python warm_models.py; \
    fi

COPY --chown=pyautoflip:pyautoflip app.py .

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "900"]
