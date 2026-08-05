# SFTP authorized keys

Put OpenSSH **public** key files in `keys/` (any filename). The compose file mounts
that directory at `/home/sftp/.ssh/keys` for [atmoz/sftp](https://github.com/atmoz/sftp)
(key-only user `sftp`, chroot home, `/syndicator` data dir).

```bash
cp ~/.ssh/sftp_client_ed25519.pub sftp/keys/mac.pub
ssh-keygen -y -f secrets/sftp_n8n_ed25519 > sftp/keys/n8n.pub
```

SSH **host** keys (server identity) live in `host_keys/` (gitignored). Generate once:

```bash
mkdir -p sftp/host_keys
ssh-keygen -t ed25519 -f sftp/host_keys/ssh_host_ed25519_key -N ''
ssh-keygen -t rsa -b 4096 -f sftp/host_keys/ssh_host_rsa_key -N ''
chmod 600 sftp/host_keys/ssh_host_*_key
```

Connect on compose port `2222` (not host `:22`). See `authorized_keys.example` for key line format.

## Permissions

The SFTP user is uid `1001` / gid `100`. Docker often creates `syndicator_sftp_data` as root, and
atmoz will not chown an already-existing `syndicator/` dir — uploads then fail with
`Permission denied`. After first `compose up` or any data migrate into the volume:

```bash
docker run --rm -v syndicator_sftp_data:/data alpine \
  sh -c 'chown -R 1001:100 /data && chmod 755 /data'
```
