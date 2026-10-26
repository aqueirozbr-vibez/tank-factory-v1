"""Unit 35: the real LiteLLM proxy on Docker (opt-in: FACTORY_DOCKER_TESTS=1).

Starts `deploy/docker-compose.proxy.yml` with a throwaway master key, creates
a key with a cap, reads its spend and revokes it; no vendor key is needed.
"""

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from orchestrator.budget.proxy import ProxyAdmin, ProxyError

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.proxy.yml"
PROJECT = "factory-proxy-test"
PORT = int(os.environ.get("FACTORY_PROXY_PORT", "4100"))

pytestmark = pytest.mark.skipif(
    os.environ.get("FACTORY_DOCKER_TESTS") != "1",
    reason="needs Docker; set FACTORY_DOCKER_TESTS=1 to run",
)


def _compose(*args: str, master: str) -> None:
    env = {**os.environ, "LITELLM_MASTER_KEY": master, "FACTORY_PROXY_PORT": str(PORT)}
    subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *args],
        env=env,
        check=True,
        timeout=900,
    )


@pytest.fixture(scope="module")
def proxy() -> object:
    master = f"sk-test-{uuid.uuid4().hex}"
    try:
        _compose("up", "-d", "--wait", master=master)
        yield master
    finally:
        _compose("down", "-v", master=master)


async def _ready(admin: ProxyAdmin) -> None:
    for _ in range(90):
        if await admin.health():
            return
        await asyncio.sleep(2)
    raise AssertionError("the proxy never became healthy")


async def test_key_lifecycle_on_the_real_proxy(proxy: object) -> None:
    admin = ProxyAdmin(f"http://127.0.0.1:{PORT}", str(proxy))
    try:
        await _ready(admin)
        minted = await admin.create_key(
            task_id="acme/demo#1",
            phase_id="integration",
            role="builder",
            max_budget_usd=0.01,
            duration_s=600,
            models=["claude-opus-5-5"],
        )
        assert minted.key.startswith("sk-") and minted.key_id and minted.key_id != minted.key
        info = await admin.key_info(minted.key)
        assert info.spend == 0.0 and info.max_budget == pytest.approx(0.01)
        assert await admin.delete_key(minted.key)
        with pytest.raises(ProxyError):
            await admin.key_info(minted.key)
    finally:
        await admin.aclose()
