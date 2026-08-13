# ADR 0002: Disposable instances

Status: accepted  
Date: 2026-08-13

## Context

ADR 0001 kept Compose as the application boundary and originally treated
volume backup, restore, and rollback as first-class `bin/syndicator` commands.
That assumed n8n SQLite, SFTP uploads, and host keys were unique state that
had to survive a host loss or a bad update.

Workflows, credentials, and webhook paths are already reconstructed from git
and `.env` by `scripts/init.sh`, Compose, and in-container reconcile. Uploaded SFTP files can be
re-provided by callers. The remaining identity is `.env`, SFTP host keys, and
authorized client keys — which belong next to other host secrets, not inside
the application lifecycle.

## Decision

Instances are disposable. Create them at will from the current checkout.

- Application volumes (`n8n_data`, `sftp_data`, `n8n_files`) are not backed up
  or restored by Syndicator. They may be lost on disaster and on update.
- `.env` and SFTP keys are ingested when an instance is created. Keeping
  callers unaware of an update means leaving that identity in place.
- Disaster recovery is a new instance: reprovide `.env`, run `scripts/init.sh`
  and `docker compose up -d --build`, then `bin/syndicator verify`. Regenerate
  SFTP keys unless they were saved outside Syndicator. Callers re-upload files
  and may need to accept a new SSH host key.
- If `.env` and SFTP keys should survive a host loss, back them up outside
  this repository. Syndicator does not choose a storage provider or encryption
  key lifecycle.

`bin/syndicator` therefore has no `backup`, `restore`, `update`, `rollback`,
`init`, or `deploy` commands. A software update is `docker compose up -d --build`
on the reviewed revision, followed by `bin/syndicator verify`.

## Consequences

- If verification fails, bring the services down; recovery is another
  `compose up` plus `verify`, not a volume restore.
- SFTP host keys remain in the `sftp_host_keys` volume so a normal container
  recreate does not change the SSH identity. Wiping that volume is visible to
  callers.
- Operators who want secret durability use their own backup of `.env` and
  `sftp/keys/`, not an application archive.

## Revisit when

- callers cannot re-upload staged files
- n8n execution history or unpublished UI edits become source of truth
- zero-downtime updates become a requirement
