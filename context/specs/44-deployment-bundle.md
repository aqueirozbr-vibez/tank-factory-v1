# Unit 44: Deployment bundle and installer

## Goal

One installation per organisation on one VM: the orchestrator, the model
proxy and the edge as Docker Compose services under one systemd unit, with
`factory install` doing every install-time step and `factory upgrade --pin`
following the release procedure (PRD §Architecture · Deployment; TRD §2.0
Packaging, §2.4, §3.4 secrets, §4.4 install log, §11 release procedure).

## Design

Terminal only. `deploy/Dockerfile` builds the orchestrator image (factory
code at the pinned tag, docker CLI, git; agents never run in it);
`deploy/docker-compose.yml` runs orchestrator, litellm with postgres and
caddy on the `factory-app` network, litellm also on the external
`factory-tasks` network under the alias `proxy`, the orchestrator's API
published on loopback for the factory CLI, the console slot reserved;
`deploy/Caddyfile` has the internal listener (console in phase 5,
`/healthz`, internal CA) and the public listener that routes only
`/webhooks/github`; `deploy/factory.service` runs the stack. The data volume
is mounted at the same path inside and outside so worktree bind mounts
resolve on the host. Secrets split by reader: `/etc/factory/factory.env`
(the orchestrator's settings and the proxy master key),
`/etc/factory/proxy.env` (master key and vendor keys, read by litellm
only), `github-app.pem` and the Ed25519 `export.key`, all 0600.

`factory install` runs in order: preflight (App settings, `.factory/`,
three dry-run issues), `/etc/factory` (App key, export key, `proxy.env`),
the data volume (hook bundle, repository clone with origin preserved),
migrations and the installation row (export public key, version),
the product-repository files committed on a `factory-install` branch
through a temporary worktree, the quality self-test, the task image,
`factory.env`, the branch-protection check (unit 43), the dry run that
derives the budget (unit 41), `docker compose up -d --build`, the systemd
unit when systemd is present, and a `/healthz` probe; every step is a line
in the install log, which ends with the export public key; a refusal stops
before anything goes live. `factory upgrade --pin` is a no-op on the same
tag; otherwise it pauses admissions (the scheduler holds the queue), drains
up to 30 min, checks out the tag, applies migrations, pulls and restarts,
runs the self-test (the eval replay joins in phase 4), records the version
and resumes, or rolls the checkout and the services back.

## Implementation

- `deploy/Dockerfile`, `deploy/entrypoint.sh`, `.dockerignore`,
  `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/factory.service`,
  `deploy/README.md`.
- `cli/install.py`: `InstallOptions`, `Installer`, `install`,
  `UpgradeOptions`, `upgrade`, `ensure_export_key`, `render_env`,
  `factory_env`, `proxy_env`, `place_clone`, `commit_product_files`.
- Writer `set_admissions_paused`, `admissions_paused`,
  `set_factory_version`, `set_export_pubkey`; `Scheduler.dispatch` holds
  while paused; `DockerCli.server_version`.
- `factory install ...` and `factory upgrade --pin --reason` commands.

## Dependencies

- Units 36, 37 and 41. Ed25519 through `cryptography` (already a
  dependency) rather than PyNaCl: recorded as a context decision.

## Verify when done

- [ ] The bundle files agree: services, networks, aliases, loopback API,
      no vendor key in the orchestrator's environment
- [ ] `factory install` on the seeded repository with the fake harness
      writes the `/etc/factory` files, the clone, the hook bundle, the
      installation row with the export key, the `factory-install` branch,
      the install log in order, and calls compose; a second run is
      idempotent; preflight refusals exit 2
- [ ] `factory upgrade` to the same tag is a no-op; a new tag runs the
      procedure and records it; a failing step rolls back
- [ ] Opt-in on Docker: `docker compose config` renders; the orchestrator
      image builds and answers `factory --version`
- [ ] The four quality commands pass
