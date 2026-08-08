# Thin wrapper around atmoz/sftp: durable host keys, key sync, data chown.
FROM atmoz/sftp:alpine

# atmoz execs /usr/sbin/sshd by absolute path — replace it with our wrapper.
RUN mv /usr/sbin/sshd /usr/sbin/sshd.real
COPY sshd-wrapper.sh /usr/sbin/sshd
COPY entrypoint.sh /syndicator-entrypoint.sh
RUN chmod +x /usr/sbin/sshd /syndicator-entrypoint.sh

ENTRYPOINT ["/syndicator-entrypoint.sh"]
