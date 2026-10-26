"""``factory proxy up|down|status``: the LiteLLM proxy on Docker Desktop (unit 35)."""

import asyncio
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from cli.common import say, settings_for, warn
from orchestrator.budget.proxy import ProxyAdmin
from orchestrator.models.settings import Settings
from orchestrator.workspace.container import ContainerError, DockerCli, ensure_network

FACTORY_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = FACTORY_ROOT / "deploy" / "docker-compose.proxy.yml"
VENDOR_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def _compose(args: list[str], *, master_key: str, port: int) -> int:
    env = Settings.subprocess_base_env()
    env["LITELLM_MASTER_KEY"] = master_key
    env["FACTORY_PROXY_PORT"] = str(port)
    for name in VENDOR_KEYS:  # the one place a vendor key goes: the proxy container
        if name in os.environ:
            env[name] = os.environ[name]
    completed = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        env=env,
        check=False,
    )
    return completed.returncode


async def _wait_healthy(admin: ProxyAdmin, *, attempts: int = 60) -> bool:
    for _ in range(attempts):
        if await admin.health():
            return True
        await asyncio.sleep(2)
    return False


def proxy(command: str, *, data_dir: Path | None) -> int:
    settings = settings_for(data_dir)
    url = settings.proxy_url or "http://127.0.0.1:4000"
    port = urlparse(url).port or 4000
    master = settings.proxy_master_key
    if command in ("up", "status") and not master:
        warn("set FACTORY_PROXY_MASTER_KEY (an sk- prefixed secret of your choosing) in .env")
        return 2
    if command == "up":
        assert master is not None
        try:
            asyncio.run(ensure_network(DockerCli(settings.docker_bin), settings.docker_network))
        except ContainerError as exc:
            warn(str(exc))
            return 1
        code = _compose(["up", "-d"], master_key=master, port=port)
        if code != 0:
            return code
        admin = ProxyAdmin(url, master)
        healthy = asyncio.run(_wait_healthy(admin))
        asyncio.run(admin.aclose())
        say(f"proxy at {url}: {'healthy' if healthy else 'not healthy yet'}")
        return 0 if healthy else 1
    if command == "down":
        return _compose(["down"], master_key=master or "sk-unused", port=port)
    if command == "status":
        assert master is not None
        admin = ProxyAdmin(url, master)
        healthy = asyncio.run(admin.health())
        asyncio.run(admin.aclose())
        say(f"proxy at {url}: {'healthy' if healthy else 'unreachable'}")
        return 0 if healthy else 1
    warn(f"unknown proxy command {command!r}")
    return 2
