"""Unit 44: the compose bundle renders and the orchestrator image builds (opt-in: Docker).

Set ``FACTORY_DOCKER_TESTS=1``. The image build pulls the Python, uv and
docker-cli images and resolves the lock file: a few minutes the first time.
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FACTORY_DOCKER_TESTS") != "1",
    reason="needs Docker; set FACTORY_DOCKER_TESTS=1 to run",
)
ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
TAG = "factory-orchestrator:test"


def docker(*args: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=timeout, cwd=ROOT
    )


def test_compose_config_renders(tmp_path: Path) -> None:
    env_file = tmp_path / "factory.env"
    env_file.write_text(
        "\n".join(
            [
                f"FACTORY_ETC_DIR={tmp_path.as_posix()}",
                f"FACTORY_DATA_DIR={tmp_path.as_posix()}",
                "FACTORY_VERSION=test",
                "FACTORY_API_PORT=18080",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "proxy.env").write_text("LITELLM_MASTER_KEY=sk-test\n", encoding="utf-8")
    rendered = docker("compose", "-f", str(COMPOSE), "--env-file", str(env_file), "config")
    assert rendered.returncode == 0, rendered.stderr
    assert "factory-orchestrator:test" in rendered.stdout
    assert "proxy" in rendered.stdout and "127.0.0.1" in rendered.stdout


def test_orchestrator_image_builds_and_reports_its_version() -> None:
    built = docker("build", "-f", "deploy/Dockerfile", "-t", TAG, ".")
    assert built.returncode == 0, built.stderr[-3000:]
    version = docker("run", "--rm", "--entrypoint", "factory", TAG, "--version", timeout=120)
    assert version.returncode == 0 and version.stdout.strip().startswith("factory ")
    cli = docker("run", "--rm", "--entrypoint", "docker", TAG, "--version", timeout=120)
    assert cli.returncode == 0 and "Docker version" in cli.stdout
