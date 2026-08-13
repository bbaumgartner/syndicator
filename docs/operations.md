# Syndicator operations

This runbook covers the supported topology: Docker Desktop on a developer Mac
and Docker Engine with the Compose plugin on one Linux host.

## Prerequisites

The host needs:

- Docker Engine or Docker Desktop with `docker compose`
- Bash, Python 3, curl, OpenSSL, and an OpenSSH client
- enough disk for the n8n and pyautoflip images and media staging
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
inputs, starts the stack, waits for the in-container n8n reconcile, and runs
end-to-end health checks. Running it again is safe. If source configuration is
unchanged and all workflows remain published, n8n import is skipped.

The first controlled deployment writes `secrets/release.env`. Unless
`SYNDICATOR_IMAGE_TAG` is explicitly set, images use the current 12-character
Git revision as their tag.

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
   an isolated stack twice, and test SFTP I/O.
3. Pull the reviewed Git revision on the server.
4. Run:

```bash
bin/syndicator deploy --pull
```

Deploy rebuilds commit-tagged images from the current checkout, starts the
stack, reconciles n8n, and verifies. Instance volumes are not snapshotted;
callers keep working when `.env`, SFTP host keys, and authorized client keys
stay in place.

An explicit tag is available for release testing:

```bash
bin/syndicator deploy --tag release-candidate-1
```

If bootstrap or verification fails, the new services are stopped and pending
details remain in `secrets/release.env.pending`. The last healthy release state
is not overwritten. Do not simply restart the failed containers; fix the
checkout and deploy again.

## Disaster recovery

Syndicator does not back up application volumes. A lost host is a new instance:

1. Install the prerequisites.
2. Check out the desired Git revision.
3. Restore `.env` from wherever you keep secrets, or recreate it and fill the
   required values.
4. Run `bin/syndicator init` and `bin/syndicator deploy`.
5. Restore authorized client public keys under `sftp/keys/` if you kept them.
6. Verify firewall, DNS, and reverse proxy.

SFTP host keys are generated on first start. Callers must accept the new host
key unless you restore the `sftp_host_keys` volume yourself. Uploaded files are
gone; callers re-upload. If you want `.env` and SFTP keys to survive a host
loss, back them up outside Syndicator.

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
project. It deploys twice, checks that an unchanged reconcile is skipped,
uploads over SFTP, and removes all test containers and volumes. A
deliberately failed release also verifies that unverified containers are
stopped and pending recovery state is recorded. A separate Buildx job verifies
n8n and pyautoflip for Linux arm64.

## Troubleshooting

If n8n is unavailable during reconcile, inspect readiness and logs:

```bash
bin/syndicator status
bin/syndicator logs n8n
```

The stack uses `/healthz/readiness`, not `/healthz`, so first-start database
migrations must finish before provisioning starts.

If SFTP host-key verification changes unexpectedly, do not delete the client
known-host entry until the cause is understood. Host keys are persistent state
in `sftp_host_keys` and survive container recreate, but not a volume wipe.

If credentials cannot be decrypted, `N8N_ENCRYPTION_KEY` in `.env` does not
match the existing `n8n_data` volume. Use the original key, or remove the
volume and let reconcile recreate credentials.

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
- configuring firewall, TLS proxy, and optional off-host secret backup
- placing `.env` and other bootstrap secrets from a vault
- checking out a reviewed Git revision and invoking `bin/syndicator deploy`

Do not duplicate Compose services, Dockerfile package installation, or n8n
bootstrap in Ansible. Terraform belongs one level further out: VM, DNS,
network rules, and storage resources only.
