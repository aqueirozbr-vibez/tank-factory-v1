# Deployment bundle (TRD §2.4, §11)

One installation per organisation on one Linux VM: the orchestrator, the
LiteLLM model proxy and the Caddy edge as Docker Compose services under one
systemd unit; task containers from the per-installation task image on the
internal `factory-tasks` network; the data volume at `/var/lib/factory`;
secrets under `/etc/factory` (mode 0600).

| File | Role |
| --- | --- |
| `Dockerfile`, `entrypoint.sh` | The orchestrator image: factory code at the pinned tag, docker CLI, git |
| `docker-compose.yml` | orchestrator, litellm (+ postgres), caddy; console slot reserved for phase 5 |
| `Caddyfile` | internal listener (console, `/healthz`, internal CA); public listener routing only `/webhooks/github` |
| `factory.service` | the systemd unit that runs `docker compose up -d` |
| `task-image/` | the task image the installer builds per installation (unit 36) |
| `hooks/` | the PreToolUse bundle copied to `/var/lib/factory/hooks` (unit 38) |
| `egress/` | the nftables rule set and tailer for the VM (unit 37) |
| `litellm/config.yaml` | generated from `.factory/roster.yaml` (unit 35) |
| `product-repo/` | the files `factory install` commits for the product repository (issue template) |

## Install

On the VM, as root, with Docker 27+ and the factory checkout at `/opt/factory`:

```
pipx install /opt/factory            # or: uv tool install /opt/factory
export FACTORY_GITHUB_APP_ID=... FACTORY_GITHUB_INSTALLATION_ID=... \
       FACTORY_GITHUB_PRIVATE_KEY_FILE=/root/factory-app.pem \
       ANTHROPIC_API_KEY=... OPENAI_API_KEY=...
factory install --repo-clone /var/lib/factory/repo --org <org> --repo <repo> \
    --issues 101 102 103 --public-host factory.example.com --console-host factory.internal
```

`factory install` runs, in order: the preflight (App settings, docker), the
`/etc/factory` files (App key, Ed25519 export key, `factory.env`,
`proxy.env`), the data volume (hook bundle, repository clone), migrations and
the installation row, the product-repository files on a `factory-install`
branch (push it and open the PR), the quality self-test, the task image, the
branch-protection check, the three-issue dry run that derives the budget,
then `docker compose up -d`, the systemd unit and a `/healthz` probe. Every
step is a line in `/var/lib/factory/install.log`, which ends with the export
public key. A refusal (self-test, protection, dry run) stops before anything
goes live.

## Upgrade

```
factory upgrade --pin v0.2.0 --reason "release 0.2.0"
```

Pauses admissions, drains running tasks (30 min at most), checks out the
tag, applies migrations, pulls and restarts the services, runs the self-test
(the eval replay joins in phase 4), then resumes admissions; a failure rolls
the checkout and the services back to the previous tag. The same tag is a
no-op.

## Development on this machine

The same compose file runs on Docker Desktop with `FACTORY_ETC_DIR` and
`FACTORY_DATA_DIR` pointing at writable folders and the `factory-tasks`
network created by `factory proxy up`. The orchestrator runs as root inside
its container because it owns the docker socket; nothing it starts runs as
root (task containers run as uid 10001).
