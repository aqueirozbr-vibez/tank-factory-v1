"""Unit 36: the container workspace with a recording fake docker, and the task image inputs."""

import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from orchestrator.harness.process import ProcessOutcome, run_with_timeout
from orchestrator.intake import Admission, Scheduler
from orchestrator.models.enums import AgentRole, BlockedReason
from orchestrator.models.settings import Settings
from orchestrator.supervisor import Supervisor
from orchestrator.trace import TraceWriter
from orchestrator.workspace.container import (
    CONTAINER_HOME,
    CliResult,
    ContainerError,
    DockerCli,
    TaskContainer,
    container_cwd,
    exec_env,
    mounts_for,
    write_env_file,
)
from orchestrator.workspace.git import run_git
from orchestrator.workspace.image import (
    FACTORY_DOCKERFILE,
    default_install_command,
    dockerfile_for,
    ensure_image,
    image_digest,
    image_tag,
)
from orchestrator.workspace.worktree import create_worktree
from tests.unit.test_admission import FakeGitHub, labeled
from tests.unit.test_build_phase import CLAIM_WRONG, GREEN
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK

IMAGE = "factory-task-acme-atlas-api:abc123def456"


HOOKS_HOST = str(Path(__file__).resolve().parents[2] / "deploy" / "hooks")
HOME_HOST = str(Path(tempfile.gettempdir()) / "factory-fake-home")


def host_argv(argv: Sequence[str]) -> list[str]:
    """The container-side hook path and python mapped to this host (FakeDocker runs here)."""
    mapped: list[str] = []
    for a in argv:
        if a.startswith("/opt/factory/hooks"):
            mapped.append(HOOKS_HOST + a[len("/opt/factory/hooks") :])
        elif a.startswith("/home/agent"):
            mapped.append(HOME_HOST + a[len("/home/agent") :])
        else:
            mapped.append(a)
    if mapped and mapped[0] == "python3":
        mapped[0] = sys.executable
    return mapped


class FakeDocker(DockerCli):
    """Records every docker call; ``exec`` runs the inner command on the host."""

    def __init__(self) -> None:
        super().__init__("docker")
        self.calls: list[list[str]] = []
        self.images: set[str] = set()
        self.containers: dict[str, str] = {}
        self.execs: list[tuple[list[str], dict[str, str], str]] = []

    async def _run(self, args: Sequence[str], *, timeout_s: float = 300) -> CliResult:
        self.calls.append(list(args))
        head = args[0]
        if head == "image":
            tag = args[-1]
            if tag not in self.images:
                raise ContainerError("no such image")
            return CliResult(0, "sha256:fake", "")
        if head == "build":
            self.images.add(args[args.index("-t") + 1])
            return CliResult(0, "", "")
        if head == "create":
            self.containers[args[args.index("--name") + 1]] = "created"
            return CliResult(0, "cid", "")
        if head == "start":
            self.containers[args[-1]] = "running"
            return CliResult(0, "", "")
        if head == "container":
            name = args[-1]
            if name not in self.containers:
                raise ContainerError("no such container")
            return CliResult(0, self.containers[name], "")
        if head == "ps":
            return CliResult(0, "\n".join(self.containers), "")
        if head == "kill":
            self.containers[args[-1]] = "exited"
            return CliResult(0, "", "")
        if head == "rm":
            self.containers.pop(args[-1], None)
            return CliResult(0, "", "")
        raise ContainerError(f"unexpected docker call {args}")

    def exec_runner(self, container: TaskContainer) -> Callable[..., object]:  # type: ignore[override]
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
            self.execs.append(
                (list(argv), exec_env(container, env), container_cwd(container.worktree, cwd))
            )
            return await run_with_timeout(
                host_argv(argv),
                cwd=cwd,
                env=env,
                timeout_s=timeout_s,
                on_stdout_line=on_stdout_line,
                on_process=on_process,
                stdin_text=stdin_text,
            )

        return run


def test_mounts_cwd_env_and_argv(product: Path, tmp_path: Path) -> None:
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    mounts = mounts_for(wt)
    assert [(m.container, m.mode) for m in mounts] == [
        ("/work", "rw"),
        ("/repo/.git", "ro"),
        ("/repo/.git/worktrees/agent-418", "rw"),
    ]
    assert mounts[0].host == str(wt.path) and mounts[1].host == str(product / ".git")
    container = TaskContainer("factory-task-418-1", IMAGE, wt.path, "agent-418")
    inside = exec_env(
        container,
        {
            "PATH": "C:\\host",
            "USERPROFILE": "C:\\Users\\x",
            "ANTHROPIC_API_KEY": "sk-vk",
            "FACTORY_PHASE_ID": "p1",
        },
    )
    assert set(inside) == {
        "ANTHROPIC_API_KEY",
        "FACTORY_PHASE_ID",
        "HOME",
        "PATH",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "FACTORY_CONTAINER",
    }
    assert (
        inside["HOME"] == CONTAINER_HOME and inside["GIT_DIR"] == "/repo/.git/worktrees/agent-418"
    )
    assert (
        inside["GIT_WORK_TREE"] == "/work" and inside["FACTORY_CONTAINER"] == "factory-task-418-1"
    )
    assert container_cwd(wt.path, wt.path / "src" / "billing") == "/work/src/billing"
    assert container_cwd(wt.path, tmp_path) == "/work"

    env_file = write_env_file({"B": "2", "A": "1"}, tmp_path / "env")
    assert env_file.read_text(encoding="utf-8") == "A=1\nB=2\n"
    with pytest.raises(ContainerError, match="newline"):
        write_env_file({"A": "1\n2"}, tmp_path / "env")

    docker = DockerCli("docker")
    argv = docker.exec_argv(
        container, argv=["claude", "-p"], cwd=wt.path / "src", env_file=env_file
    )
    assert argv == [
        "docker",
        "exec",
        "-i",
        "-u",
        "10001:10001",
        "-w",
        "/work/src",
        "--env-file",
        str(env_file),
        "factory-task-418-1",
        "claude",
        "-p",
    ]
    create = docker.create_argv(
        image=IMAGE,
        name="factory-task-418-1",
        mounts=mounts,
        network="factory-tasks",
        labels={"factory.task": TASK},
    )
    assert create[:3] == ["create", "--name", "factory-task-418-1"]
    assert "--init" in create and create[create.index("--user") + 1] == "10001:10001"
    assert create[create.index("--workdir") + 1] == "/work"
    assert create[create.index("--network") + 1] == "factory-tasks"
    assert create.count("-v") == 3 and create[-3:] == [IMAGE, "sleep", "infinity"]
    assert f"factory.task={TASK}" in create


def test_image_inputs_tag_and_dockerfile(product: Path) -> None:
    assert dockerfile_for(product) == FACTORY_DOCKERFILE
    assert default_install_command(product) is None
    first = image_digest(product, FACTORY_DOCKERFILE, None)
    (product / ".factory").mkdir(exist_ok=True)
    (product / ".factory" / "factory.yaml").write_text("concurrency_n: 2\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=product)
    run_git(["commit", "-q", "-m", "factory change"], cwd=product)
    second = image_digest(product, FACTORY_DOCKERFILE, None)
    (product / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=product)
    run_git(["commit", "-q", "-m", "lockfile"], cwd=product)
    third = image_digest(product, FACTORY_DOCKERFILE, None)
    assert len({first, second, third}) == 3 and all(len(d) == 12 for d in (first, second, third))
    assert default_install_command(product) == "uv sync --frozen"
    assert image_digest(product, FACTORY_DOCKERFILE, "make install") != third
    tag = image_tag("Acme", "Atlas API", third)
    assert tag == f"factory-task-acme-atlas-api:{third}"
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*:[a-z0-9]{12}", tag)
    text = FACTORY_DOCKERFILE.read_text(encoding="utf-8")
    assert "useradd -m -u 10001" in text and "UV_PROJECT_ENVIRONMENT=/opt/venv" in text
    assert 'CMD ["sleep", "infinity"]' in text and "| bash" not in text
    (product / ".devcontainer").mkdir()
    (product / ".devcontainer" / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    assert dockerfile_for(product) == product / ".devcontainer" / "Dockerfile"


async def test_ensure_image_builds_once(product: Path) -> None:
    docker = FakeDocker()
    first = await ensure_image(product, org="acme", repo_name="atlas-api", docker=docker)
    assert first.built and first.tag.startswith("factory-task-acme-atlas-api:")
    build = next(c for c in docker.calls if c[0] == "build")
    assert "--build-arg" in build and "INSTALL_COMMAND=" in build
    assert build[build.index("-f") + 1].endswith("Dockerfile.factory")
    second = await ensure_image(product, org="acme", repo_name="atlas-api", docker=docker)
    assert not second.built and second.tag == first.tag
    forced = await ensure_image(
        product, org="acme", repo_name="atlas-api", docker=docker, force=True
    )
    assert forced.built


async def test_runner_container_mode_creates_runs_inside_and_removes(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    docker = FakeDocker()
    stack = Stack(
        trace_writer,
        product,
        tmp_path,
        [PLAN, GREEN, ACCEPTED],
        workspace="container",
        docker=docker,
        task_image=IMAGE,
    )
    stack.admit()
    report = await stack.runner.run(TASK)
    assert report.delivered
    create = next(c for c in docker.calls if c[0] == "create")
    assert create[create.index("--name") + 1] == "factory-task-418-1" and IMAGE in create
    assert ["start", "factory-task-418-1"] in docker.calls
    assert ["rm", "-f", "factory-task-418-1"] in docker.calls and docker.containers == {}
    assert docker.execs, "phases ran through the container runner"
    for _argv, env, cwd in docker.execs:
        assert cwd == "/work" and "USERPROFILE" not in env and env["GIT_WORK_TREE"] == "/work"
    harness_envs = [c.env for c in stack.harness.calls]
    assert all("FACTORY_CONTAINER" not in env for env in harness_envs)  # added at exec time only
    events = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE payload_json LIKE '%factory-task-418-1%'"
    )
    assert len(events) >= 2


async def test_container_removed_on_a_block_and_admission_records_the_kind(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    docker = FakeDocker()
    stack = Stack(
        trace_writer,
        product,
        tmp_path,
        [PLAN, CLAIM_WRONG],
        workspace="container",
        docker=docker,
        task_image=IMAGE,
    )
    github = FakeGitHub()
    admission = Admission(
        writer=trace_writer,
        github=github,
        settings=Settings(data_dir=tmp_path / "data", workspace="container"),
        repo_clone=product,
        scheduler=Scheduler(trace_writer, Supervisor(lambda _tid: _noop())),
    )
    result = await admission.admit(labeled(418))
    assert result.task_id == TASK and stack.task()["workspace_kind"] == "container"
    report = await stack.runner.run(TASK)
    assert report.blocked_reason is BlockedReason.REPAIR_CAP
    assert ["rm", "-f", "factory-task-418-1"] in docker.calls and docker.containers == {}
    assert stack.runner.deps.build_deps(AgentRole.BUILDER).runner is run_with_timeout


async def _noop() -> None:
    return None
