"""The per-installation task image: built at install, rebuilt when its inputs change (TRD §2.0).

The digest covers the Dockerfile, the install command, the ``.factory``
tree at ``main`` and the lockfiles at ``main``; the tag embeds it, so a
change on ``main`` to any of them yields a new tag and a rebuild.
"""

import hashlib
import re
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from orchestrator import constants
from orchestrator.workspace.container import DockerCli
from orchestrator.workspace.git import run_git

FACTORY_DOCKERFILE = Path(__file__).resolve().parents[2] / "deploy" / "task-image" / "Dockerfile"
TAG_SAFE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class ImageBuild:
    tag: str
    built: bool
    dockerfile: Path
    digest: str


def dockerfile_for(repo: Path) -> Path:
    candidate = repo / ".devcontainer" / "Dockerfile"
    return candidate if candidate.is_file() else FACTORY_DOCKERFILE


def default_install_command(repo: Path, *, ref: str = "main") -> str | None:
    """``uv sync --frozen`` when the repository locks with uv; otherwise nothing."""
    probe = run_git(["cat-file", "-e", f"{ref}:uv.lock"], cwd=repo, check=False)
    return "uv sync --frozen" if probe.returncode == 0 else None


def image_digest(
    repo: Path, dockerfile: Path, install_command: str | None, *, ref: str = "main"
) -> str:
    digest = hashlib.sha256()
    digest.update(dockerfile.read_bytes())
    digest.update(b"\0" + (install_command or "").encode("utf-8") + b"\0")
    tree = run_git(["rev-parse", "-q", "--verify", f"{ref}:.factory"], cwd=repo, check=False)
    digest.update((tree.stdout.strip() if tree.returncode == 0 else "no-factory-dir").encode())
    for lockfile in sorted(constants.LOCKFILES):
        blob = run_git(["rev-parse", "-q", "--verify", f"{ref}:{lockfile}"], cwd=repo, check=False)
        if blob.returncode == 0:
            digest.update(f"\0{lockfile}={blob.stdout.strip()}".encode())
    return digest.hexdigest()[:12]


def image_tag(org: str, repo: str, digest: str) -> str:
    name = TAG_SAFE.sub("-", f"factory-task-{org}-{repo}".lower()).strip("-")
    return f"{name}:{digest}"


def export_context(repo: Path, target: Path, *, ref: str = "main") -> None:
    """A clean copy of ``ref`` as the build context: no data directories, no secrets."""
    archive = target.parent / "context.tar"
    run_git(["archive", "--format=tar", "-o", str(archive), ref], cwd=repo)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(target, filter="data")
    archive.unlink()


async def ensure_image(
    repo: Path,
    *,
    org: str,
    repo_name: str,
    docker: DockerCli,
    install_command: str | None = None,
    ref: str = "main",
    force: bool = False,
) -> ImageBuild:
    dockerfile = dockerfile_for(repo)
    command = (
        install_command if install_command is not None else default_install_command(repo, ref=ref)
    )
    digest = image_digest(repo, dockerfile, command, ref=ref)
    tag = image_tag(org, repo_name, digest)
    if not force and await docker.image_exists(tag):
        return ImageBuild(tag, False, dockerfile, digest)
    with tempfile.TemporaryDirectory(prefix="factory-image-") as scratch:
        context = Path(scratch) / "context"
        export_context(repo, context, ref=ref)
        build_dockerfile = context / "Dockerfile.factory"
        build_dockerfile.write_bytes(dockerfile.read_bytes())
        await docker.build(
            tag,
            context=context,
            dockerfile=build_dockerfile,
            build_args={"INSTALL_COMMAND": command or ""},
        )
    return ImageBuild(tag, True, dockerfile, digest)
