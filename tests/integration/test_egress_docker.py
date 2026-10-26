"""Unit 37: the internal network on Docker (opt-in: FACTORY_DOCKER_TESTS=1).

From a container on the internal network, GitHub and PyPI are unreachable
while a container aliased ``proxy`` on the same network answers.
"""

import os
import subprocess
import uuid

import pytest

from orchestrator.workspace.container import DockerCli, ensure_network

pytestmark = pytest.mark.skipif(
    os.environ.get("FACTORY_DOCKER_TESTS") != "1",
    reason="needs Docker; set FACTORY_DOCKER_TESTS=1 to run",
)
IMAGE = "python:3.12-slim"
PROBE = (
    "import sys, urllib.request\n"
    "try:\n"
    "    urllib.request.urlopen(sys.argv[1], timeout=8)\n"
    "except Exception as exc:\n"
    "    print('blocked', type(exc).__name__)\n"
    "    sys.exit(3)\n"
    "print('reached')\n"
)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=300
    )


async def test_internal_network_blocks_the_internet_and_allows_the_proxy_alias() -> None:
    network = f"factory-tasks-test-{uuid.uuid4().hex[:8]}"
    docker = DockerCli()
    assert await ensure_network(docker, network) == network
    server = f"proxy-{uuid.uuid4().hex[:8]}"
    try:
        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            server,
            "--network",
            network,
            "--network-alias",
            "proxy",
            IMAGE,
            "python3",
            "-m",
            "http.server",
            "4000",
        )
        for url in ("https://api.github.com", "https://pypi.org/simple/"):
            probe = _docker(
                "run", "--rm", "--network", network, IMAGE, "python3", "-c", PROBE, url, check=False
            )
            assert probe.returncode == 3 and "blocked" in probe.stdout, (
                url,
                probe.stdout,
                probe.stderr,
            )
        reached = _docker(
            "run",
            "--rm",
            "--network",
            network,
            IMAGE,
            "python3",
            "-c",
            PROBE,
            "http://proxy:4000/",
            check=False,
        )
        assert reached.returncode == 0 and "reached" in reached.stdout, (
            reached.stdout,
            reached.stderr,
        )
    finally:
        _docker("rm", "-f", server, check=False)
        _docker("network", "rm", network, check=False)
