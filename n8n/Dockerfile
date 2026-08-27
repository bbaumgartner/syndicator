# Custom n8n image: static ffmpeg/ffprobe + locked community nodes.

FROM mwader/static-ffmpeg:7.1.1@sha256:11a44711684c0b9f754c047dcd64235b8b52deab251bd0e0a86f22faa160749c AS ffmpeg

FROM docker.io/n8nio/n8n:2.36.6@sha256:ac17f6df352c221d17ad2d566e5e61835bcc36de1e912650c8dced43d8cccf76

USER root

COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

# Seed community packages outside the data volume. The entrypoint copies them
# into /home/node/.n8n/nodes when the lock changes.
WORKDIR /opt/n8n-nodes-seed
COPY package.json package-lock.json ./
RUN npm ci --omit=dev \
	&& chown -R node:node /opt/n8n-nodes-seed

COPY entrypoint.sh /entrypoint-syndicator.sh
COPY reconcile.js /opt/syndicator/reconcile.js
COPY hash-owner-password.js /opt/syndicator/hash-owner-password.js
COPY workflows /opt/syndicator/workflows
COPY credentials /opt/syndicator/credentials
RUN chmod +x /entrypoint-syndicator.sh \
	&& chown -R node:node /opt/syndicator

USER node
WORKDIR /home/node

ENTRYPOINT ["tini", "--", "/entrypoint-syndicator.sh"]
