# Custom n8n image: static ffmpeg/ffprobe + locked community nodes.

FROM mwader/static-ffmpeg:9.0.2-1@sha256:f5d100184884eb3e82b0eb14bb5ebf98da99826ac824989720297fb9ef21c822 AS ffmpeg

FROM docker.io/n8nio/n8n:2.45.4@sha256:80e23ced901f0e22597d4f3464e3aaf9d835372262ea6f08ef760d8ca35df25c

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
