# Custom n8n image: static ffmpeg/ffprobe + community nodes (Postiz, FFmpeg Studio).
# Base tag stays :stable so scripts/update.sh can pull security patches;
# override N8N_BASE_IMAGE to pin a digest/version when needed.

ARG N8N_BASE_IMAGE=docker.n8n.io/n8nio/n8n:stable
ARG FFMPEG_IMAGE=mwader/static-ffmpeg:7.1.1

FROM ${FFMPEG_IMAGE} AS ffmpeg

FROM ${N8N_BASE_IMAGE}

ARG POSTIZ_VERSION=0.2.17
ARG FFMPEG_STUDIO_VERSION=1.0.0

USER root

COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

# Seed community packages outside the data volume. The entrypoint copies them
# into /home/node/.n8n/nodes on first start (volume mount hides image content).
RUN mkdir -p /opt/n8n-nodes-seed \
	&& cd /opt/n8n-nodes-seed \
	&& npm init -y \
	&& npm install --omit=dev \
		"n8n-nodes-postiz@${POSTIZ_VERSION}" \
		"n8n-nodes-ffmpeg-studio@${FFMPEG_STUDIO_VERSION}" \
	&& chown -R node:node /opt/n8n-nodes-seed

COPY entrypoint.sh /entrypoint-syndicator.sh
RUN chmod +x /entrypoint-syndicator.sh

USER node

ENTRYPOINT ["tini", "--", "/entrypoint-syndicator.sh"]
