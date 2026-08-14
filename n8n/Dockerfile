# Custom n8n image: static ffmpeg/ffprobe + locked community nodes.

FROM mwader/static-ffmpeg:9.0@sha256:b90574a4e2ae62b763c39c384526689e7eb435da6398f4fb3f6c3f1c6a14ce33 AS ffmpeg

FROM docker.io/n8nio/n8n:2.33.7@sha256:3989d9b8ebb77b4ee8f604519eb73e44f4384bfaa689526e0104eed79a237d30

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
