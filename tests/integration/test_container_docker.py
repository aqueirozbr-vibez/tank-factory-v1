"""Unit 36: a real task container on Docker (opt-in: FACTORY_DOCKER_TESTS=1).

Uses python:3.12-slim as the task image: the environment inside is exactly
the allowed set, a fake-harness sleep runs inside, a timeout kills the
container, and removal leaves nothing behind.
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from orchestrator import constants
from orchestrator.harness.base import HarnessRequest
from orchestrator.harness.fake import FakeHarness, FakeScript
from orchestrator.models.enums import AgentRole
from orchestrator.workspace.container import DockerCli, create_container, exec_env
from orchestrator.workspace.hooks import verify_hook
from orchestrator.workspace.worktree import create_worktree

pytestmark = pytest.mark.skipif(
    os.environ.get("FACTORY_DOCKER_TESTS") != "1",
    reason="needs Docker; set FACTORY_DOCKER_TESTS=1 to run",
)
IMAGE = os.environ.get("FACTORY_TEST_TASK_IMAGE", "python:3.12-slim")
ROOT = Path(__file__).resolve().parents[2]


def image_env(image: str) -> set[str]:
    """The variable names the image itself declares (PATH, LANG, GPG_KEY, PYTHON_VERSION...)."""
    shown = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return {entry.split("=", 1)[0] for entry in json.loads(shown.stdout)}


async def test_container_env_sleep_timeout_and_removal(product: Path, tmp_path: Path) -> None:
    docker = DockerCli(env_dir=tmp_path / "env")
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    name = "factory-task-test-418"
    container = await create_container(
        docker,
        image=IMAGE,
        name=name,
        worktree=wt,
        network=None,
        task_id="test/task#418",
        hooks_dir=ROOT / "deploy" / "hooks",
    )
    try:
        assert await docker.state(name) == "running"
        runner = docker.exec_runner(container)
        lines: list[str] = []
        outcome = await runner(
            ["env"],
            cwd=wt.path,
            env={"FACTORY_PHASE_ID": "p1", "ANTHROPIC_API_KEY": "sk-vk-test"},
            timeout_s=60,
            on_stdout_line=lines.append,
        )
        assert outcome.exit_code == 0
        seen = {line.split("=", 1)[0] for line in lines if "=" in line}
        expected = set(
            exec_env(container, {"FACTORY_PHASE_ID": "p1", "ANTHROPIC_API_KEY": "sk-vk-test"})
        )
        assert expected <= seen
        # Nothing beyond the exec env except what the image declares and what docker sets
        # on every exec (HOSTNAME, HOME): no host variable and no key reaches the container.
        extra = seen - expected - image_env(IMAGE) - {"HOSTNAME", "HOME"}
        assert not extra, extra
        assert any(line == "ANTHROPIC_API_KEY=sk-vk-test" for line in lines)
        assert any(line == "HOME=/home/agent" for line in lines)

        pwd: list[str] = []
        await runner(
            ["python3", "-c", "import os; print(os.getcwd())"],
            cwd=wt.path / "src",
            env={},
            timeout_s=60,
            on_stdout_line=pwd.append,
        )
        assert pwd == ["/work/src"]

        # The hook bundle is mounted read-only and denies inside the container.
        await verify_hook(runner, hooks_dir=constants.HOOKS_MOUNT, python="python3", cwd=wt.path)
        allowed = await runner(
            ["python3", f"{constants.HOOKS_MOUNT}/{constants.HOOK_SCRIPT}"],
            cwd=wt.path,
            env={},
            timeout_s=60,
            stdin_text=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}),
        )
        assert allowed.exit_code == 0
        readonly = await runner(
            ["sh", "-c", f"touch {constants.HOOKS_MOUNT}/x 2>&1; echo exit=$?"],
            cwd=wt.path,
            env={},
            timeout_s=60,
            on_stdout_line=lines.append,
        )
        assert readonly.exit_code == 0 and lines[-1] == "exit=1"

        harness = FakeHarness(
            [FakeScript(sleep_s=30, envelope={"status": "success"})],
            python="python3",
            runner=runner,
        )
        result = await harness.run(
            HarnessRequest(
                role=AgentRole.BUILDER,
                prompt="sleep",
                output_schema={},
                cwd=wt.path,
                env={},
                model="m",
                timeout_s=2,
            ),
            on_tool_call=lambda _call: None,
        )
        assert result.outcome == "timeout"
        assert await docker.state(name) != "running"  # the timeout killed the container
    finally:
        await docker.remove(name)
    assert await docker.state(name) == "gone"


async def test_cancelled_exec_kills_the_container(product: Path, tmp_path: Path) -> None:
    """A budget stop or the kill-switch cancels the exec; nothing inside may live on."""
    docker = DockerCli(env_dir=tmp_path / "env")
    wt = create_worktree(product, issue=419, worktrees_dir=tmp_path / "w")
    name = "factory-task-test-419"
    container = await create_container(
        docker, image=IMAGE, name=name, worktree=wt, network=None, task_id="test/task#419"
    )
    try:
        runner = docker.exec_runner(container)
        job = asyncio.ensure_future(runner(["sleep", "60"], cwd=wt.path, env={}, timeout_s=120))
        await asyncio.sleep(2)
        job.cancel("budget")
        with pytest.raises(asyncio.CancelledError):
            await job
        assert await docker.state(name) != "running"
    finally:
        await docker.remove(name)
