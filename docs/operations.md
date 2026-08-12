# Syndicator operations

This runbook covers the supported topology: Docker Desktop on a developer Mac
and Docker Engine with the Compose plugin on one Linux host.

## Prerequisites

The host needs:

- Docker Engine or Docker Desktop with `docker compose`
- Bash, Python 3, curl, OpenSSL, and an OpenSSH client
- enough disk for the n8n and pyautoflip images, media staging, and backups
- amd64 execution support for the SFTP image; Docker Desktop supplies
  emulation on Apple Silicon

Check the host without changing anything:

```bash
bin/syndicator doctor
```

## First installation

Create the local configuration:

```bash
bin/syndicator init
```

On the first run this creates `.env`, generates `N8N_ENCRYPTION_KEY`, and stops
with a list of values that still need input. Fill in:

- n8n owner email and password
- OpenAI API key
- Postiz API key
- the public URL and bind addresses appropriate for the host

The lifecycle parses `.env` as data, never as shell code. Quote values that
contain spaces or `#`; single quotes preserve `$`, backticks, and other
characters literally.

Then deploy:

```bash
bin/syndicator deploy
```

`deploy` performs initialization and diagnostics again, builds immutable
inputs, starts the stack, reconciles n8n credentials and workflows, and runs
end-to-end health checks. Running it again is safe. If source configuration is
unchanged and all workflows remain published, n8n import is skipped.

The first controlled deployment writes `secrets/release.env`. Unless
`SYNDICATOR_IMAGE_TAG` is explicitly set, images use the current 12-character
Git revision as their tag. The exact Compose/workflow source is retained under
`secrets/release-sources/`, so routine commands continue to target the running
release even after the checkout moves forward or a rollback completes.

## Local and network configuration

The checked-in defaults bind n8n and SFTP to `127.0.0.1`. This is safe for
local development but not reachable by another LAN machine.

For trusted LAN access, set the required bind addresses explicitly:

```dotenv
N8N_BIND_ADDRESS=0.0.0.0
SFTP_BIND_ADDRESS=0.0.0.0
N8N_HOST=192.0.2.10
N8N_WEBHOOK_URL=http://192.0.2.10:5678/
```

Use the real host address, not the documentation address above. Restrict access
with the host firewall.

For an internet-facing host:

- keep `N8N_BIND_ADDRESS=127.0.0.1`
- terminate HTTPS in Caddy, nginx, or another reverse proxy
- set `N8N_WEBHOOK_URL=https://.../`, `N8N_PROTOCOL=https`, and
  `N8N_SECURE_COOKIE=true`
- set `N8N_PROXY_HOPS=1` when there is one trusted reverse proxy
- expose SFTP only to required source addresses

The current webhook workflows do not authenticate requests. Do not publish the
n8n port directly to an untrusted network. Adding webhook authentication is an
application contract change and must be coordinated with callers.

## Routine commands

Inspect status:

```bash
bin/syndicator status
bin/syndicator logs
```

Run non-mutating service and contract checks:

```bash
bin/syndicator verify
```

Reconcile n8n after a workflow or credential-template change:

```bash
bin/syndicator bootstrap
```

Export workflows after editing them in n8n:

```bash
bin/syndicator export
python3 -m unittest discover -s tests -p 'test_*.py'
```

Exports are normalized: pin data, instance IDs, project ownership, and version
metadata are removed. Review the resulting JSON before committing it.

If the owner password changes, update `N8N_OWNER_PASSWORD` in `.env`, then run:

```bash
scripts/ensure-n8n-owner.sh --force
bin/syndicator deploy
```

After adding or removing a public key under `sftp/keys/`, apply it through the
supported hook:

```bash
bin/syndicator restart sftp
```

## Dependency updates

Container, npm, pip, and GitHub Actions dependencies are proposed weekly by
Dependabot. Do not edit a floating `latest` or `stable` tag on the server.

For an update:

1. Review the release notes and dependency diff.
2. Let CI validate manifests, audit npm dependencies, build both images, deploy
   an isolated stack twice, test SFTP I/O, restore a backup, and exercise
   rollback.
3. Pull the reviewed Git revision on the server.
4. Run:

```bash
bin/syndicator update
```

When the Git revision changes, update creates a consistent pre-update backup,
builds commit-tagged images, deploys, and verifies. Previous images are not
pruned because rollback needs them.

An explicit tag is available for release testing:

```bash
bin/syndicator update --tag release-candidate-1
```

If bootstrap or verification fails, the new services are stopped and recovery
details remain in `secrets/release.env.pending`. The last healthy release state
is not overwritten. Use the recorded backup with the matching Git revision;
do not simply restart the failed containers.

## Backups

Create a backup:

```bash
bin/syndicator backup
```

The command briefly stops stateful services so SQLite and SFTP data are
consistent. It archives:

- `n8n_data`
- `sftp_data`
- `sftp_host_keys`
- `.env`, n8n owner/API/bootstrap state, SFTP client keys, and release state
- a manifest containing SHA-256 checksums and the Git revision

The shared processing directory `n8n_files` is scratch space and is not backed
up. InsightFace models are checksum-pinned inside the pyautoflip image, not
stored in a mutable volume.

Archives default to `backups/` and mode `0600`. They still contain plaintext
credentials. Copy them to encrypted off-host storage and apply an external
retention policy; the repository deliberately does not choose a storage
provider or encryption key lifecycle.

To select a destination:

```bash
bin/syndicator backup --output /secure/path/syndicator.tar.gz
```

## Restore and disaster recovery

Restore is destructive and requires explicit confirmation:

```bash
bin/syndicator restore --yes /secure/path/syndicator.tar.gz
```

Before changing state, restore rejects unsafe archive paths, unsupported
members, missing critical volume archives, unsupported formats, and checksum
mismatches. It also requires the checkout to match the archive's Git revision.
Only after validating inner volume archives and building or locating the
required images does it stop services and cross the destructive boundary. It
then replaces current configuration and critical volumes, starts the stack,
reconciles n8n, and verifies all services.

If a post-mutation restore step fails, all restored-but-unverified services are
stopped and recovery context is written beside `release.env` with the suffix
`.restore-pending`.

For disaster recovery on a new host:

1. Install the prerequisites.
2. Check out the Git revision recorded in `manifest.json` inside the backup.
3. Place the encrypted backup on the host and decrypt it locally.
4. Run the restore command.
5. Verify firewall, DNS, reverse proxy, and off-host backup scheduling.

`--no-build` is reserved for rollback or for a restore where the exact tagged
images are already present.

## Rollback

Rollback is available after a release-changing update:

```bash
bin/syndicator rollback
```

It requires:

- `PREVIOUS_TAG` and `ROLLBACK_BACKUP` in `secrets/release.env`
- a clean checkout at the recorded current Git revision
- both previous application images still present locally
- the matching pre-update backup

Rollback refuses dirty or mismatched current source. Before restoring the
previous release, it uses the current lifecycle to back up the current release.
It then selects the retained source bundle for the exact previous Git revision,
uses the current hardened restore implementation with that revision's
Compose/workflow definitions, restores matching data, starts the previous image
tags, verifies the stack, and swaps the current and previous release records.

Do not use `docker image prune -a` while rollback retention is required.

## Testing

Fast checks:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/test-init.sh
bash tests/validate-compose.sh
npm audit --prefix n8n
```

Full isolated test:

```bash
bash tests/validate-compose.sh build n8n pyautoflip
bash tests/integration/reframe.sh
bash tests/integration/stack.sh
```

CI first builds the production model-warmed image and performs a real reframe.
The stack integration test uses random loopback ports and a unique Compose
project. It deploys twice, checks that API keys and resources are not
duplicated, uploads over SFTP, validates backup/restore, deploys a second
release tag, rolls back, and removes all test containers and volumes. A
deliberately failed release also verifies that untrusted containers are
stopped and pending recovery state is recorded; the same containment is tested
for a failed restore. A separate Buildx job verifies n8n and pyautoflip for
Linux arm64.

## Troubleshooting

If bootstrap reports n8n as unavailable, inspect readiness and logs:

```bash
bin/syndicator status
bin/syndicator logs n8n
```

The stack uses `/healthz/readiness`, not `/healthz`, so first-start database
migrations must finish before provisioning starts.

If SFTP host-key verification changes unexpectedly, do not delete the client
known-host entry until the cause is understood. Host keys are persistent state
in `sftp_host_keys` and are included in backups.

If credentials cannot be decrypted after a restore, the
`N8N_ENCRYPTION_KEY` does not match `n8n_data`. Restore `.env` and the volume
from the same archive.

If Apple Silicon reports an SFTP platform warning, confirm
`SFTP_PLATFORM=linux/amd64`; the image is intentionally emulated.

The n8n image may log that its internal Python runner is absent. Syndicator
uses JavaScript Code nodes and pyautoflip as a separate Python service, so no
Python n8n runner is required. If untrusted users gain workflow-edit access,
move JavaScript execution to n8n's external runner model instead of adding
Python to the main container.

## Optional host automation

Ansible is intentionally not required for application operation. Add it when
the Linux machine itself must be recreated automatically. Its responsibilities
should stop at:

- installing a reviewed Docker Engine/Compose version and host utilities
- creating the deployment user and directory
- configuring firewall, TLS proxy, and encrypted off-host backup transport
- placing `.env` and other bootstrap secrets from a vault
- checking out a reviewed Git revision and invoking `bin/syndicator deploy`

Do not duplicate Compose services, Dockerfile package installation, n8n
bootstrap, or backup logic in Ansible. Terraform belongs one level further
out: VM, DNS, network rules, and storage resources only.
