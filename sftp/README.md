# SFTP authorized keys

Put OpenSSH **public** key files in `keys/` (any filename). The compose file mounts
that directory at `/home/sftp/.ssh/keys` for [atmoz/sftp](https://github.com/atmoz/sftp)
(key-only user `sftp`, chroot home, `/syndicator` data dir).

```bash
cp ~/.ssh/sftp_client_ed25519.pub sftp/keys/mac.pub
ssh-keygen -y -f secrets/sftp_n8n_ed25519 > sftp/keys/n8n.pub
```

See `authorized_keys.example` for the expected key line format.
