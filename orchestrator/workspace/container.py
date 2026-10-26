"""One Docker container per task over the ``docker`` binary (TRD §2.0, §3.1, §5).

The worktree is bind-mounted at ``/work``; the clone's ``.git`` is mounted
read-only with the worktree's admin directory read-write, and ``GIT_DIR`` /
``GIT_WORK_TREE`` point at them, so git works inside without host paths.
Each phase command runs as ``docker exec`` with an environment file; a
timeout kills the container so nothing inside survives it.
"""

import asyncio
import contextlib
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from orchestrator import constants
from orchestrator.errors import FactoryError
from orchestrator.harness.claude_code import Runner
from orchestrator.harness.process import ProcessOutcome, run_with_timeout
from orchestrator.models.settings import Settings
from orchestrator.workspace.worktree import Worktree

MOUNT_WORK = "/work"
MOUNT_REPO_GIT = "/repo/.git"
AGENT_UID = 10001
CONTAINER_HOME = "/home/agent"
CONTAINER_PATH = "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
HOST_ONLY_ENV = frozenset(
    {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"}
)
GIT_SAFE_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.directory",
    "GIT_CONFIG_VALUE_0": "*",
}


class ContainerError(FactoryError):
    code = "container_error"


@dataclass(frozen=True)
class Mount:
    host: str
    container: str
    mode: str

    def flag(self) -> str:
        return f"{self.host}:{self.container}:{self.mode}"


@dataclass(frozen=True)
class TaskContainer:
    name: str
    image: str
    worktree: Path
    admin_name: str

    @property
    def git_dir(self) -> str:
        return f"{MOUNT_REPO_GIT}/worktrees/{self.admin_name}"


@dataclass(frozen=True)
class CliResult:
    exit_code: int | None
    stdout: str
    stderr_tail: str


def mounts_for(
    worktree: Worktree, hooks_dir: Path | None = None, extra: Sequence[Mount] = ()
) -> list[Mount]:
    repo_git = worktree.repo / ".git"
    if not repo_git.is_dir():
        raise ContainerError(f"{worktree.repo} has no .git directory to mount")
    admin = repo_git / "worktrees" / worktree.path.name
    if not admin.is_dir():
        raise ContainerError(f"{worktree.path} has no worktree admin directory under {repo_git}")
    mounts = [
        Mount(str(worktree.path), MOUNT_WORK, "rw"),
        Mount(str(repo_git), MOUNT_REPO_GIT, "ro"),
        Mount(str(admin), f"{MOUNT_REPO_GIT}/worktrees/{worktree.path.name}", "rw"),
    ]
    if hooks_dir is not None:
        mounts.append(Mount(str(hooks_dir), constants.HOOKS_MOUNT, "ro"))
    mounts.extend(extra)
    return mounts


def git_env(container: TaskContainer) -> dict[str, str]:
    return {"GIT_DIR": container.git_dir, "GIT_WORK_TREE": MOUNT_WORK, **GIT_SAFE_ENV}


def container_cwd(worktree: Path, cwd: Path) -> str:
    """The host ``cwd`` mapped under ``/work``; anything outside the worktree maps to ``/work``."""
    try:
        relative = cwd.resolve().relative_to(worktree.resolve())
    except ValueError:
        return MOUNT_WORK
    return str(PurePosixPath(MOUNT_WORK).joinpath(*relative.parts))


def exec_env(container: TaskContainer, env: Mapping[str, str]) -> dict[str, str]:
    """The explicit environment inside the container: the phase env, HOME, PATH and git."""
    inside = {k: v for k, v in env.items() if k not in HOST_ONLY_ENV}
    inside["HOME"] = CONTAINER_HOME
    inside["PATH"] = CONTAINER_PATH
    inside.update(git_env(container))
    inside["FACTORY_CONTAINER"] = container.name
    if "CODEX_HOME" in inside:
        inside["CODEX_HOME"] = constants.CONTAINER_CODEX_HOME  # a host path means nothing inside
    return inside


def write_env_file(env: Mapping[str, str], directory: Path) -> Path:
    """A 0600 env file for ``docker exec --env-file``; keys never travel on the command line."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix="phase-", suffix=".env", delete=False
    ) as handle:
        for key, value in sorted(env.items()):
            if "\n" in value or "\n" in key:
                raise ContainerError(f"environment value for {key} contains a newline")
            handle.write(f"{key}={value}\n")
        path = Path(handle.name)
    os.chmod(path, 0o600)
    return path


class DockerCli:
    """The Docker CLI with the base environment; every call is explicit argv."""

    def __init__(self, binary: str = "docker", *, env_dir: Path | None = None) -> None:
        self.binary = binary
        self.env_dir = env_dir or Path(tempfile.gettempdir()) / "factory-env"

    async def _run(self, args: Sequence[str], *, timeout_s: float = 300) -> CliResult:
        lines: list[str] = []
        outcome = await run_with_timeout(
            [self.binary, *args],
            cwd=Path.cwd(),
            env=Settings.subprocess_base_env(),
            timeout_s=timeout_s,
            on_stdout_line=lines.append,
        )
        if outcome.exit_code != 0:
            shown = " ".join(args[:2])
            raise ContainerError(
                f"docker {shown} failed ({outcome.exit_code}): {outcome.stderr_tail[-300:]}"
            )
        return CliResult(outcome.exit_code, "\n".join(lines), outcome.stderr_tail)

    # --- images -------------------------------------------------------------------------

    async def image_exists(self, tag: str) -> bool:
        try:
            await self._run(["image", "inspect", "--format", "{{.Id}}", tag], timeout_s=60)
        except ContainerError:
            return False
        return True

    async def build(
        self, tag: str, *, context: Path, dockerfile: Path, build_args: Mapping[str, str]
    ) -> None:
        args = ["build", "-t", tag, "-f", str(dockerfile)]
        for key, value in build_args.items():
            args += ["--build-arg", f"{key}={value}"]
        await self._run([*args, str(context)], timeout_s=3600)

    async def server_version(self) -> str | None:
        """The daemon's version, or ``None`` when docker is unreachable."""
        try:
            result = await self._run(["version", "--format", "{{.Server.Version}}"], timeout_s=60)
        except ContainerError:
            return None
        return result.stdout.strip() or None

    # --- networks -------------------------------------------------------------------------

    async def network_inspect(self, name: str) -> bool | None:
        """``True``/``False`` for the network's internal flag; ``None`` when it does not exist."""
        try:
            result = await self._run(
                ["network", "inspect", "--format", "{{.Internal}}", name], timeout_s=60
            )
        except ContainerError:
            return None
        return result.stdout.strip().lower() == "true"

    async def network_create(self, name: str, *, internal: bool = True) -> None:
        args = ["network", "create", "--driver", "bridge"]
        if internal:
            args.append("--internal")
        await self._run([*args, "--label", "factory.network=tasks", name], timeout_s=120)

    async def task_containers_by_ip(self, network: str) -> dict[str, tuple[str, str]]:
        """Source IP → (container name, task id) for the factory's containers on ``network``."""
        try:
            result = await self._run(
                [
                    "ps",
                    "--filter",
                    "label=factory.task",
                    "--format",
                    '{{.Names}}\t{{.Label "factory.task"}}',
                ],
                timeout_s=60,
            )
        except ContainerError:
            return {}
        mapping: dict[str, tuple[str, str]] = {}
        for line in result.stdout.splitlines():
            name, _, task_id = line.partition("\t")
            if not name:
                continue
            try:
                shown = await self._run(
                    [
                        "inspect",
                        "--format",
                        '{{with index .NetworkSettings.Networks "'
                        + network
                        + '"}}{{.IPAddress}}{{end}}',
                        name,
                    ],
                    timeout_s=60,
                )
            except ContainerError:
                continue
            ip = shown.stdout.strip()
            if ip:
                mapping[ip] = (name, task_id)
        return mapping

    async def task_container_names(self) -> list[str]:
        """Every container the factory labelled, running or not (the kill-switch)."""
        try:
            result = await self._run(
                ["ps", "-a", "--filter", "label=factory.task", "--format", "{{.Names}}"],
                timeout_s=60,
            )
        except ContainerError:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # --- containers -----------------------------------------------------------------------

    def create_argv(
        self,
        *,
        image: str,
        name: str,
        mounts: Sequence[Mount],
        network: str | None,
        labels: Mapping[str, str],
    ) -> list[str]:
        args = [
            "create",
            "--name",
            name,
            "--init",
            "--user",
            f"{AGENT_UID}:{AGENT_UID}",
            "--workdir",
            MOUNT_WORK,
            "--env",
            f"HOME={CONTAINER_HOME}",
        ]
        if network:
            args += ["--network", network]
        for key, value in labels.items():
            args += ["--label", f"{key}={value}"]
        for mount in mounts:
            args += ["-v", mount.flag()]
        return [*args, image, "sleep", "infinity"]

    async def create(
        self,
        *,
        image: str,
        name: str,
        mounts: Sequence[Mount],
        network: str | None,
        labels: Mapping[str, str],
    ) -> None:
        argv = self.create_argv(
            image=image, name=name, mounts=mounts, network=network, labels=labels
        )
        await self._run(argv, timeout_s=600)

    async def start(self, name: str) -> None:
        await self._run(["start", name])

    async def state(self, name: str) -> str:
        """``running``, ``exited``, ... or ``gone`` when no container has that name."""
        try:
            result = await self._run(
                ["container", "inspect", "--format", "{{.State.Status}}", name], timeout_s=60
            )
        except ContainerError:
            return "gone"
        return result.stdout.strip()

    async def exists(self, name: str) -> bool:
        return await self.state(name) != "gone"

    async def kill(self, name: str, *, grace_s: float = constants.PROCESS_GRACE_S) -> None:
        """TERM to the container, a grace period, then KILL (TRD §5.4, §6.4)."""
        try:
            await self._run(["kill", "--signal", "TERM", name], timeout_s=60)
        except ContainerError:
            return
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if await self.state(name) != "running":
                return
            await asyncio.sleep(0.5)
        with contextlib.suppress(ContainerError):
            await self._run(["kill", name], timeout_s=60)

    async def remove(self, name: str) -> None:
        with contextlib.suppress(ContainerError):
            await self._run(["rm", "-f", name], timeout_s=120)

    # --- exec -----------------------------------------------------------------------------

    def exec_argv(
        self, container: TaskContainer, *, argv: Sequence[str], cwd: Path, env_file: Path
    ) -> list[str]:
        return [
            self.binary,
            "exec",
            "-i",
            "-u",
            f"{AGENT_UID}:{AGENT_UID}",
            "-w",
            container_cwd(container.worktree, cwd),
            "--env-file",
            str(env_file),
            container.name,
            *argv,
        ]

    def exec_runner(self, container: TaskContainer) -> Runner:
        """A ``Runner`` that executes each phase command inside the container."""

        async def run(
            argv: Sequence[str],
            *,
            cwd: Path,
            env: Mapping[str, str],
            timeout_s: float,
            on_stdout_line: Callable[[str], None] | None = None,
            on_process: Callable[[int], None] | None = None,
            stdin_text: str | None = None,
        ) -> ProcessOutcome:
            env_file = write_env_file(exec_env(container, env), self.env_dir)
            try:
                outcome = await run_with_timeout(
                    self.exec_argv(container, argv=argv, cwd=cwd, env_file=env_file),
                    cwd=Path.cwd(),
                    env=Settings.subprocess_base_env(),
                    timeout_s=timeout_s,
                    on_stdout_line=on_stdout_line,
                    on_process=on_process,
                    stdin_text=stdin_text,
                )
            except asyncio.CancelledError:
                await self.kill(container.name)  # budget or kill-switch: nothing inside lives on
                raise
            finally:
                env_file.unlink(missing_ok=True)
            if outcome.timed_out:
                await self.kill(container.name)  # the exec client died; nothing inside may live on
            return outcome

        return run


async def create_container(
    docker: DockerCli,
    *,
    image: str,
    name: str,
    worktree: Worktree,
    network: str | None,
    task_id: str,
    hooks_dir: Path | None = None,
    extra_mounts: Sequence[Mount] = (),
) -> TaskContainer:
    """Create and start the task's container; a stale one with the same name is replaced."""
    if await docker.exists(name):
        await docker.remove(name)
    await docker.create(
        image=image,
        name=name,
        mounts=mounts_for(worktree, hooks_dir, extra_mounts),
        network=network,
        labels={"factory.task": task_id, "factory.branch": worktree.branch},
    )
    await docker.start(name)
    return TaskContainer(name, image, worktree.path, worktree.path.name)


async def ensure_network(docker: DockerCli, name: str) -> str:
    """The task network: created ``--internal`` (no route out); an open one is refused."""
    internal = await docker.network_inspect(name)
    if internal is None:
        await docker.network_create(name, internal=True)
        return name
    if not internal:
        raise ContainerError(
            f"docker network {name!r} exists but is not internal; remove it or choose another name"
        )
    return name
