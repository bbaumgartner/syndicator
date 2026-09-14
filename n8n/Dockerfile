# Custom n8n image: static ffmpeg/ffprobe + locked community nodes.

FROM mwader/static-ffmpeg:9.0.1@sha256:54e55b0cb8f672870fc38ceb2e6c411855cb3b39c505f5f3b2505ee01ed5f2b7 AS ffmpeg

FROM docker.io/n8nio/n8n:2.38.7@sha256:a8c95f75c6fdf65f5f2b7a7b354744eaa1c62bb911b5c00af6499c3f38e4cd32

USER root

COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

# Seed community packages outside the data volume. The entrypoint copies them
# into /home/node/.n8n/nodes when the lock changes.
WORKDIR /opt/n8n-nodes-seed
COPY package.json package-lock.json ./
COPY patch-ffmpeg-timeout.js /opt/syndicator/patch-ffmpeg-timeout.js
# Community nodes ignore their Timeout setting and always use runFfmpeg's 300s default.
# Ignore install scripts: this image has no compiler, and DHI Alpine omits
# /etc/alpine-release so isolated-vm would load the glibc prebuild and crash.
# Copy the musl prebuild into the path node-gyp-build checks first.
RUN npm ci --omit=dev --ignore-scripts \
	&& node -e 'const fs=require("fs"),os=require("os"),path=require("path"); const dir="/opt/n8n-nodes-seed/node_modules/isolated-vm"; const src=path.join(dir,"prebuilds","linux-"+os.arch(),"isolated-vm.abi"+process.versions.modules+".musl.node"); const dest=path.join(dir,"build","Release"); fs.mkdirSync(dest,{recursive:true}); fs.copyFileSync(src,path.join(dest,"isolated_vm.node"));' \
	&& node /opt/syndicator/patch-ffmpeg-timeout.js /opt/n8n-nodes-seed \
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
