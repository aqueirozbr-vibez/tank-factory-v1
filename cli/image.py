"""``factory image build``: the per-installation task image (unit 36)."""

import asyncio
from pathlib import Path

from cli.common import say, settings_for, warn
from orchestrator.config import load_factory_config
from orchestrator.workspace.container import ContainerError, DockerCli
from orchestrator.workspace.image import ensure_image


async def _build(
    *, repo_clone: Path, org: str, repo: str, force: bool, data_dir: Path | None
) -> int:
    settings = settings_for(data_dir)
    repo_clone = repo_clone.resolve()
    config = load_factory_config(repo_clone / ".factory")
    docker = DockerCli(settings.docker_bin, env_dir=settings.data_dir / "env")
    try:
        built = await ensure_image(
            repo_clone,
            org=org,
            repo_name=repo,
            docker=docker,
            install_command=config.settings.install_command,
            force=force,
        )
    except ContainerError as exc:
        warn(str(exc))
        return 1
    say(f"{built.tag} ({'built' if built.built else 'present'}; dockerfile {built.dockerfile})")
    return 0


def build_image(
    *, repo_clone: Path, org: str, repo: str, force: bool, data_dir: Path | None
) -> int:
    return asyncio.run(
        _build(repo_clone=repo_clone, org=org, repo=repo, force=force, data_dir=data_dir)
    )
