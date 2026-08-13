# ADR 0001: Compose as the application boundary

Status: accepted  
Date: 2026-08-12

## Context

Syndicator runs three materially different services on one machine:

- n8n/Node with ffmpeg and two community node packages
- pyautoflip/Python with native media and machine-learning dependencies
- a chrooted OpenSSH SFTP endpoint

The previous setup mixed a small declarative Compose file with a large,
stateful host bootstrap. Image tags floated, workflow IDs were duplicated in
scripts, and bootstrap read n8n's private SQLite schema. This made Docker
appear to be the source of the complexity, although most fragility was in the
imperative lifecycle around it.

The supported target for the next one to two years is a developer Mac and one
production Linux host. Repeatability, testability, and low operator effort are
more important than eliminating containers.

## Decision

Keep Docker Compose as the application packaging and runtime boundary.

- Dockerfiles own language runtimes, native libraries, ffmpeg, community
  packages, and model preparation.
- Compose owns service networking, health, startup dependencies, ports, and
  persistent volumes.
- `bin/syndicator` is the only operator-facing lifecycle. It delegates to
  focused scripts for initialization, deployment, reconciliation, and
  verification.
- Runtime inputs are pinned. Dependency changes arrive as reviewable pull
  requests and must pass an isolated full-stack test before deployment.
- Each changed release is tagged by Git revision. Application volumes are
  disposable; identity (`.env` and SFTP keys) is supplied at instantiate time.
  Volume backup and rollback are out of scope; see
  [ADR 0002](0002-disposable-instances.md).

Ansible may be added outside this boundary to prepare a Linux host: install
Docker, configure a firewall or reverse proxy, place the repository and
encrypted secrets, and invoke `bin/syndicator deploy`. It must not reproduce
the application installation, workflow import, or update logic.

Terraform is reserved for infrastructure resources such as a VM, DNS records,
firewall rules, and backup storage. It is not used to configure processes or
packages inside the host.

## Alternatives considered

### Native shell installer

Rejected. It would need to reconcile Node/n8n, npm packages, two ffmpeg
installations, Python 3.12 and native ML libraries, model downloads, OpenSSH
users and chroot permissions, systemd units, and macOS/Linux differences.
Making that installer idempotent and reversible would recreate a container
runtime poorly.

### Native Ansible services

Viable only if removing Docker becomes a hard operational requirement.
Ansible improves idempotency over shell, but the role would still own all
language runtimes, system packages, users, permissions, and service units. A
Linux VM test matrix would also replace the current Mac/Linux parity.

### Ansible wrapping Compose

Compatible with this decision. It becomes worthwhile when rebuilding the
production host itself is frequent or when firewall, TLS, and secret placement
need to be managed. For one host it remains optional so the application does
not acquire a second mandatory control plane.

### Puppet

Rejected for the current scale. Its long-lived host convergence model is
valuable for fleets of managed machines, not one application host.

### Terraform

Rejected as an application installer. Provisioners or remote-exec would make
host changes less testable and less idempotent. Terraform can still create the
host around Syndicator.

### Kubernetes or Nomad

Rejected. The stack is single-host, uses SQLite and local shared storage, and
does not need scheduling or high availability. A cluster orchestrator would
add more state and failure modes than it removes.

### Direct n8n access to the SFTP data volume

Accepted. n8n mounts `sftp_data` at `/syndicator` and uses Read/Write File
nodes. The external SFTP interface stays stable for callers. Revisit if
services move to separate machines.

## Consequences

- Docker remains a prerequisite, but operators need only the lifecycle
  commands documented in [operations.md](../operations.md).
- The SFTP base is amd64-only and runs through Docker Desktop emulation on
  Apple Silicon. The production Linux host should preferably be amd64.
- n8n's internal JavaScript task runner is accepted for this trusted,
  single-user deployment. External runners should be evaluated before
  untrusted users can edit workflows.
- The stack does not provide TLS or webhook authentication. Safe defaults bind
  published ports to loopback; exposing them requires an explicit network and
  reverse-proxy decision.
- pyautoflip's Python graph and model archive are hash-pinned, but Debian media
  packages still come from the live Bookworm repositories. Rebuild from the
  same Git revision if a bit-for-bit image recreate is required; use a Debian
  snapshot if that recreate must be independent of current Bookworm.

## Revisit when

- more than a few hosts need centralized convergence
- n8n moves to PostgreSQL/queue mode or services move to separate machines
- untrusted users can author workflows
- zero-downtime deployment or high availability becomes a requirement
- the external SFTP contract is retired
