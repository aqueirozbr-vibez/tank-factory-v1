# Unit 36: Task image and container workspace

## Goal

From phase 3 an agent phase and the quality commands run inside a Docker
container per task built from a per-installation image, with the task's
worktree bind-mounted, the container user `agent` (uid 10001), an explicit
environment, and `docker kill` after the grace period (TRD §2.0, §3.1,
§5). The image is built at install from the repository's `.devcontainer`
Dockerfile or the factory base image plus the repository's install
command, and rebuilt when `.factory/` or a lockfile changes on `main`.
Containers run on Docker Desktop on this PC (user decision, 2026-10-24).

## Design

Terminal only. `factory image build` prints the tag; with
`FACTORY_WORKSPACE=container` every task row carries
`workspace_kind = container`, a `container` log event names the image and
the container, and the container is gone when the task leaves `running`.

## Implementation

### deploy/task-image/Dockerfile

- Debian slim with Python 3.12, `uv` (copied from its image), Node 22 and
  the Claude Code and Codex CLIs (copied from the official Node image, no
  `curl | bash`), git, ripgrep, tini; user `agent` 10001 with its home;
  the repository copied in at build and the `INSTALL_COMMAND` run with
  `UV_PROJECT_ENVIRONMENT=/opt/venv` so baked dependencies survive the
  worktree mount over `/work`; `CMD sleep infinity`.

### orchestrator/workspace/image.py

- `dockerfile_for(repo)`: `.devcontainer/Dockerfile` when present, else
  the factory Dockerfile. `image_digest(repo, dockerfile, install_command)`
  hashes the Dockerfile, the install command, the `.factory` tree at `main`
  and the lockfile blobs at `main`; `image_tag(org, repo, digest)`.
  `ensure_image(...)` builds from a clean `git archive` of `main` when the
  tag is missing.

### orchestrator/workspace/container.py

- `DockerCli` (create, start, kill, rm, exists, image checks, build,
  `exec_runner`) over the `docker` binary with the base environment.
- `mounts_for(worktree)`: the worktree at `/work` read-write, the clone's
  `.git` at `/repo/.git` read-only, the worktree's admin directory
  read-write; `git_env()` sets `GIT_DIR`, `GIT_WORK_TREE` and
  `safe.directory` so git works inside without host paths.
- `exec_runner(container)`: a `Runner` that runs each phase command as
  `docker exec -i -u 10001:10001 -w <mapped cwd> --env-file <0600 file>`;
  on timeout it kills the container (TERM, grace, KILL) so nothing inside
  survives; the env file never carries the key on the command line.
- `create_container(docker, *, image, name, worktree, network)`.

### Wiring

- `Settings.workspace` (`worktree` | `container`), `task_image`,
  `docker_bin`, `docker_network`; `FactorySettings.install_command`.
- `make_harness(..., runner=)`; `FakeHarness.with_runner`.
- `RunnerDeps.docker`, `task_image`, `harness_for`; the runner creates the
  container after the worktree, builds the phase deps with the container's
  runner, and removes the container when the run ends.
- `Admission` records the workspace kind from settings; `build_service`
  ensures the image; `factory image build`.

## Dependencies

- Docker Desktop (installed). Unit tests use a recording fake docker whose
  exec runs locally; the container tests are opt-in with
  `FACTORY_DOCKER_TESTS=1` and use `python:3.12-slim`.

## Verify when done

- [ ] Mounts, exec argv, cwd mapping and the env file are exact; the env
      inside holds exactly the allowed variables
- [ ] Image digest changes with `.factory` or a lockfile; the tag is a valid
      lowercase Docker reference; the Dockerfile has the agent user, the venv
      path and no piped installer
- [ ] In container mode the runner creates `factory-task-<issue>-<attempt>`
      with the image, runs the phases through the container runner, records
      `workspace_kind = container`, and removes the container when the task
      leaves `running`, on success and on a block
- [ ] Opt-in: a real container shows exactly the allowed variables, a fake
      harness sleep runs inside and a timeout kills the container
- [ ] The four quality commands pass
