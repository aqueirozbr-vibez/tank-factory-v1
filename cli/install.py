"""``factory install`` and ``factory upgrade --pin``: one installation on one VM (TRD §2.4, §11).

Install: preflight, the ``/etc/factory`` files (App key, Ed25519 export key,
``factory.env``, ``proxy.env``), the data volume (hook bundle, repository
clone), migrations and the installation row, the product-repository files
on a ``factory-install`` branch, the self-test, the task image, the
branch-protection check, the dry run that derives the budget, then the
compose stack, the systemd unit and a ``/healthz`` probe. Every step is a
line in the install log; a refusal stops before anything goes live.

Upgrade: pause admissions, drain, check out the tag, migrate, pull and
restart, self-test, resume or roll back. The same tag is a no-op.
"""

import asyncio
import base64
import getpass
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from cli.budget import _dry_run as dry_run_issues
from cli.common import open_trace, say, settings_for, warn
from cli.protection import protection_report
from orchestrator import constants
from orchestrator.budget.dry_run import InstallLog
from orchestrator.config import load_factory_config
from orchestrator.errors import ConfigError, FactoryError
from orchestrator.gates.selftest import SelfTestReport, assert_self_test, self_test
from orchestrator.harness.process import run_with_timeout
from orchestrator.models.config import QualityCommands
from orchestrator.models.enums import EventKind
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.version import factory_version
from orchestrator.workspace.container import DockerCli, ensure_network
from orchestrator.workspace.git import GitError, run_git
from orchestrator.workspace.hooks import default_hooks_dir, verify_hook
from orchestrator.workspace.image import ensure_image

FACTORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = FACTORY_ROOT / "deploy"
COMPOSE_FILE = DEPLOY / "docker-compose.yml"
SERVICE_FILE = DEPLOY / "factory.service"
PRODUCT_FILES = DEPLOY / "product-repo"
MIGRATIONS = FACTORY_ROOT / "migrations"
SYSTEMD_UNIT = Path("/etc/systemd/system/factory.service")
VENDOR_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
INSTALL_BRANCH = "factory-install"
HEALTH_WAIT_S = 180
DRAIN_POLL_S = 10

Commands = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Probe = Callable[[str], bool]
SelfTester = Callable[..., Awaitable[SelfTestReport]]


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        env=Settings.subprocess_base_env(),
    )


def probe_health(url: str) -> bool:
    try:
        return httpx.get(url, timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


@dataclass
class InstallOptions:
    repo_clone: Path
    org: str
    repo: str
    pin: str | None = None
    harness: str = "roster"
    scripts_dir: Path | None = None
    etc_dir: Path = Path("/etc/factory")
    data_dir: Path = Path("/var/lib/factory")
    issues: tuple[int, ...] = ()
    skip_image: bool = False
    skip_dry_run: bool = False
    skip_protection: bool = False
    no_compose: bool = False
    console_host: str = "factory.internal"
    public_host: str | None = None
    acme_email: str | None = None
    api_port: int = 8080
    vendor_env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))


# --- files under /etc/factory ----------------------------------------------------------------


def write_private(path: Path, text: str) -> None:
    """A 0600 file (the mode is a no-op on Windows); parents are created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def render_env(values: Mapping[str, str]) -> str:
    lines = []
    for key in sorted(values):
        value = values[key]
        if "\n" in value or "\n" in key:
            raise ConfigError(f"environment value for {key} contains a newline")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def ensure_export_key(etc_dir: Path) -> str:
    """The Ed25519 export key (generated once, 0600); returns the public key, base64."""
    key_file = etc_dir / "export.key"
    if key_file.is_file():
        loaded = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        if not isinstance(loaded, ed25519.Ed25519PrivateKey):
            raise ConfigError(f"{key_file} is not an Ed25519 private key")
        private = loaded
    else:
        private = ed25519.Ed25519PrivateKey.generate()
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        write_private(key_file, pem.decode("ascii"))
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(public).decode("ascii")


def factory_env(
    opts: InstallOptions,
    *,
    settings: Settings,
    master_key: str,
    service_token: str,
    clone: Path,
    task_image: str | None,
    version: str,
) -> dict[str, str]:
    """The orchestrator's environment: settings, compose variables, no vendor key."""
    env = {
        "FACTORY_VERSION": version,
        "FACTORY_ORG": opts.org,
        "FACTORY_REPO": opts.repo,
        "FACTORY_HARNESS": opts.harness,
        "FACTORY_REPO_CLONE": str(clone),
        "FACTORY_ETC_DIR": str(opts.etc_dir),
        "FACTORY_DATA_DIR": str(opts.data_dir),
        "FACTORY_WORKSPACE": "container",
        "FACTORY_HOOKS_DIR": str(opts.data_dir / "hooks"),
        "FACTORY_PROXY_URL": "http://litellm:4000",
        "FACTORY_PROXY_URL_IN_TASKS": "http://proxy:4000",
        "FACTORY_PROXY_MASTER_KEY": master_key,
        "FACTORY_SERVICE_TOKEN": service_token,
        "FACTORY_GITHUB_APP_ID": str(settings.github_app_id),
        "FACTORY_GITHUB_INSTALLATION_ID": str(settings.github_installation_id),
        "FACTORY_GITHUB_PRIVATE_KEY_FILE": str(opts.etc_dir / "github-app.pem"),
        "FACTORY_EXPORT_KEY_FILE": str(opts.etc_dir / "export.key"),
        "FACTORY_TRUSTED_PROXY_HEADER": "X-Forwarded-For",
        "FACTORY_TIMEZONE": settings.timezone,
        "FACTORY_BIND": "0.0.0.0",
        "FACTORY_PORT": "8080",
        "FACTORY_API_PORT": str(opts.api_port),
        "FACTORY_CONSOLE_HOST": opts.console_host,
        "FACTORY_PUBLIC_HOST": opts.public_host or "localhost",
        "FACTORY_ACME_EMAIL": opts.acme_email or "admin@localhost",
    }
    if settings.webhook_secret:
        env["FACTORY_WEBHOOK_SECRET"] = settings.webhook_secret
    if opts.scripts_dir is not None:
        env["FACTORY_SCRIPTS_DIR"] = str(opts.scripts_dir)
    if task_image:
        env["FACTORY_TASK_IMAGE"] = task_image
    return env


def proxy_env(master_key: str, vendor_env: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """The proxy container's environment: the master key and the vendor keys present."""
    present = [name for name in VENDOR_KEYS if vendor_env.get(name)]
    env = {"LITELLM_MASTER_KEY": master_key}
    env.update({name: vendor_env[name] for name in present})
    return env, present


# --- the data volume ---------------------------------------------------------------------------


def place_clone(source: Path, data_dir: Path) -> Path:
    """The repository clone the containers see: under the data volume, origin preserved."""
    source = source.resolve()
    target = (data_dir / "repo").resolve()
    if source == target or data_dir.resolve() in source.parents:
        return source
    if (target / ".git").exists():
        return target
    data_dir.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "-q", str(source), str(target)], cwd=data_dir)
    origin = run_git(["remote", "get-url", "origin"], cwd=source, check=False)
    if origin.returncode == 0 and origin.stdout.strip():
        run_git(["remote", "set-url", "origin", origin.stdout.strip()], cwd=target)
    return target


def commit_product_files(clone: Path) -> tuple[str, str | None]:
    """The product-repository files on ``factory-install`` without touching the checkout."""
    scratch = Path(tempfile.mkdtemp(prefix="factory-install-"))
    worktree = scratch / "wt"
    run_git(["worktree", "add", "-q", "-B", INSTALL_BRANCH, str(worktree), "HEAD"], cwd=clone)
    try:
        copied: list[str] = []
        for src in sorted(PRODUCT_FILES.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(PRODUCT_FILES)
            dst = worktree / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel.as_posix())
        if not copied:
            return INSTALL_BRANCH, None
        run_git(["add", "-A"], cwd=worktree)
        run_git(
            [
                "-c",
                "user.name=factory",
                "-c",
                "user.email=factory@localhost",
                "commit",
                "-q",
                "-m",
                "chore(factory): issue template and factory files (factory install)",
            ],
            cwd=worktree,
        )
        return INSTALL_BRANCH, run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    finally:
        run_git(["worktree", "remove", "--force", str(worktree)], cwd=clone, check=False)
        shutil.rmtree(scratch, ignore_errors=True)


def known_failing_fixture(clone: Path) -> Path:
    local = clone / ".factory-dev" / "known_failing"
    return local if local.is_dir() else FACTORY_ROOT / "fixtures" / "known_failing"


# --- install -------------------------------------------------------------------------------------


class Installer:
    def __init__(
        self, opts: InstallOptions, *, commands: Commands = run_command, probe: Probe = probe_health
    ) -> None:
        self.opts = opts
        self.commands = commands
        self.probe = probe
        self.log = InstallLog(opts.data_dir)

    def step(self, name: str, **payload: object) -> None:
        self.log.append({"kind": "install", "step": name, **payload})
        detail = ", ".join(f"{k}={v}" for k, v in payload.items() if k != "checks")
        say(f"install · {name}: {detail}" if detail else f"install · {name}")

    async def run(self) -> int:
        opts = self.opts
        settings = settings_for(opts.data_dir)
        missing = [
            name
            for name, value in (
                ("FACTORY_GITHUB_APP_ID", settings.github_app_id),
                ("FACTORY_GITHUB_INSTALLATION_ID", settings.github_installation_id),
                ("FACTORY_GITHUB_PRIVATE_KEY_FILE", settings.github_private_key_file),
            )
            if value is None
        ]
        if missing:
            warn(f"set {', '.join(missing)} before installing")
            return 2
        assert settings.github_private_key_file is not None
        if not (opts.repo_clone / ".factory").is_dir():
            warn(f"{opts.repo_clone} has no .factory/ directory")
            return 2
        if not opts.skip_dry_run and len(opts.issues) != constants.DRY_RUN_ISSUES:
            warn(
                f"--issues needs exactly {constants.DRY_RUN_ISSUES} issue numbers"
                " (or --skip-dry-run)"
            )
            return 2
        version = opts.pin or factory_version()
        master_key = settings.proxy_master_key or f"sk-{secrets.token_urlsafe(32)}"
        service_token = settings.service_token or secrets.token_urlsafe(32)

        # /etc/factory
        opts.etc_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(opts.etc_dir, stat.S_IRWXU)
        pem = settings.github_private_key_file.read_text(encoding="utf-8")
        write_private(opts.etc_dir / "github-app.pem", pem)
        pubkey = ensure_export_key(opts.etc_dir)
        proxy_values, vendors = proxy_env(master_key, opts.vendor_env)
        write_private(opts.etc_dir / "proxy.env", render_env(proxy_values))
        self.step("etc", dir=str(opts.etc_dir), export_pubkey=pubkey, vendor_keys=vendors)

        # the data volume
        opts.data_dir.mkdir(parents=True, exist_ok=True)
        hooks = opts.data_dir / "hooks"
        shutil.copytree(
            default_hooks_dir(),
            hooks,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        await verify_hook(
            run_with_timeout, hooks_dir=str(hooks), python=sys.executable, cwd=opts.data_dir
        )
        clone = place_clone(opts.repo_clone, opts.data_dir)
        self.step("data", clone=str(clone), hooks=str(hooks))

        # migrations and the installation row
        config = load_factory_config(clone / ".factory")
        db = TraceDB.open(opts.data_dir / "trace.db", migrations_dir=MIGRATIONS)
        try:
            writer = TraceWriter(db)
            writer.load_config_version(**config.config_version_row())
            if writer.get_installation() is None:
                writer.insert_installation(
                    org=opts.org,
                    repo=opts.repo,
                    factory_version=version,
                    config_sha=config.config_sha,
                    per_task_budget_usd=constants.DRY_RUN_PROVISIONAL_BUDGET_USD,
                    export_pubkey=pubkey,
                    concurrency_n=config.settings.concurrency_n,
                    timezone=settings.timezone,
                )
            else:
                writer.set_export_pubkey(pubkey)
                writer.set_factory_version(version)
            writer.write_event(
                EventKind.LOG,
                "orchestrator",
                payload={
                    "install": "trace",
                    "factory_version": version,
                    "config_sha": config.config_sha,
                },
            )
        finally:
            db.close()
        self.step(
            "trace",
            db=str(opts.data_dir / "trace.db"),
            migrations=len(sorted(MIGRATIONS.glob("*.sql"))),
            factory_version=version,
            config_sha=config.config_sha,
        )

        branch, sha = commit_product_files(clone)
        self.step("product_repo", branch=branch, commit=sha)

        report = await self_test(
            config.quality, known_failing_fixture(clone), env=settings.subprocess_base_env()
        )
        assert_self_test(report)
        self.step("self_test", checks=[c.note for c in report.checks], ok=report.ok)

        task_image: str | None = None
        if not opts.skip_image:
            docker = DockerCli(settings.docker_bin, env_dir=opts.data_dir / "env")
            if await docker.server_version() is None:
                raise ConfigError(
                    "the docker daemon is unreachable; install and start Docker first"
                )
            await ensure_network(docker, settings.docker_network)
            built = await ensure_image(
                clone,
                org=opts.org,
                repo_name=opts.repo,
                docker=docker,
                install_command=config.settings.install_command,
            )
            task_image = built.tag
            self.step("image", tag=task_image, built=built.built)

        write_private(
            opts.etc_dir / "factory.env",
            render_env(
                factory_env(
                    opts,
                    settings=settings,
                    master_key=master_key,
                    service_token=service_token,
                    clone=clone,
                    task_image=task_image,
                    version=version,
                )
            ),
        )
        self.step("env", file=str(opts.etc_dir / "factory.env"))

        if not opts.skip_protection:
            protection = await protection_report(
                settings, org=opts.org, repo=opts.repo, branch=None
            )
            self.log.append(protection.record())
            if not protection.required:
                warn(f"refusing to go live: {protection.detail}")
                return 1
            self.step("protection", branch=protection.branch, source=protection.source)

        derived = next(
            (
                r
                for r in reversed(self.log.records())
                if r.get("kind") == "dry_run" and not r.get("refused")
            ),
            None,
        )
        if not opts.skip_dry_run and derived is not None:
            self.step("dry_run", kept=derived.get("per_task_budget_usd"))
        elif not opts.skip_dry_run:
            code = await dry_run_issues(
                repo=clone,
                issues=list(opts.issues),
                harness="fake" if opts.harness == "fake" else None,
                data_dir=opts.data_dir,
                org=opts.org,
                skip_self_test=True,
                repo_name=opts.repo,
            )
            if code != 0:
                warn("refusing to go live: the dry run did not derive a budget")
                return 1

        if not opts.no_compose:
            up = self.commands(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE_FILE),
                    "--env-file",
                    str(opts.etc_dir / "factory.env"),
                    "up",
                    "-d",
                    "--build",
                ]
            )
            if up.returncode != 0:
                raise ConfigError(f"docker compose up failed: {up.stderr[-500:]}")
            self.step("compose", up=True)
            if Path("/run/systemd/system").is_dir() and hasattr(os, "geteuid"):
                SYSTEMD_UNIT.write_text(SERVICE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
                self.commands(["systemctl", "daemon-reload"])
                self.commands(["systemctl", "enable", "factory"])
                self.step("systemd", unit=str(SYSTEMD_UNIT))
            url = f"http://127.0.0.1:{opts.api_port}/healthz"
            deadline = time.monotonic() + HEALTH_WAIT_S
            healthy = self.probe(url)
            while not healthy and time.monotonic() < deadline:
                await asyncio.sleep(5)
                healthy = self.probe(url)
            self.step("healthz", url=url, ok=healthy)
            if not healthy:
                warn(f"{url} is not green after {HEALTH_WAIT_S} s; check `docker compose logs`")
                return 1

        self.log.append(
            {"kind": "install", "step": "done", "export_pubkey": pubkey, "factory_version": version}
        )
        say(
            f"installed {opts.org}/{opts.repo} at {version}; export public key {pubkey};"
            f" log {self.log.path}; next: push `{branch}` and open its PR, then"
            f" `export FACTORY_ENV_FILE={opts.etc_dir / 'factory.env'}` for the factory CLI"
        )
        return 0


def install(
    opts: InstallOptions, *, commands: Commands = run_command, probe: Probe = probe_health
) -> int:
    try:
        return asyncio.run(Installer(opts, commands=commands, probe=probe).run())
    except (FactoryError, GitError) as exc:
        warn(f"install stopped: {exc}")
        return 1


# --- upgrade -------------------------------------------------------------------------------------


@dataclass
class UpgradeOptions:
    pin: str
    reason: str
    actor: str | None = None
    factory_root: Path = FACTORY_ROOT
    etc_dir: Path = Path("/etc/factory")
    data_dir: Path = Path("/var/lib/factory")
    drain_minutes: int = 30


async def upgrade_async(
    opts: UpgradeOptions,
    *,
    commands: Commands = run_command,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    selftest: SelfTester = self_test,
) -> int:
    settings = settings_for(opts.data_dir)
    who = opts.actor or getpass.getuser()
    db, writer = open_trace(settings)
    log = InstallLog(opts.data_dir)
    try:
        installation = writer.get_installation()
        if installation is None:
            warn("no installation row; run `factory install` first")
            return 2
        current = str(installation["factory_version"])
        if current == opts.pin:
            say(f"factory is already at {opts.pin}; nothing to do")
            return 0
        compose = [
            "docker",
            "compose",
            "-f",
            str(opts.factory_root / "deploy" / "docker-compose.yml"),
            "--env-file",
            str(opts.etc_dir / "factory.env"),
        ]
        writer.write_event(
            EventKind.CONTROL,
            who,
            payload={
                "action": "factory_upgrade",
                "from": current,
                "to": opts.pin,
                "reason": opts.reason,
            },
        )
        writer.set_admissions_paused(True)
        log.append({"kind": "upgrade", "step": "pause", "from": current, "to": opts.pin})
        deadline = clock() + opts.drain_minutes * 60
        while writer.count_running() > 0 and clock() < deadline:
            await sleep(DRAIN_POLL_S)
        log.append({"kind": "upgrade", "step": "drain", "running": writer.count_running()})

        def git(*args: str) -> None:
            done = commands(["git", "-C", str(opts.factory_root), *args])
            if done.returncode != 0:
                raise ConfigError(f"git {' '.join(args)} failed: {done.stderr[-300:]}")

        try:
            git("fetch", "--tags", "origin")
            git("checkout", "-q", opts.pin)
            TraceDB.open(
                settings.trace_db_path, migrations_dir=opts.factory_root / "migrations"
            ).close()
            log.append(
                {
                    "kind": "upgrade",
                    "step": "migrations",
                    "dir": str(opts.factory_root / "migrations"),
                }
            )
            for args in (
                ["pull", "--ignore-buildable"],
                ["up", "-d", "--build", "--remove-orphans"],
            ):
                done = commands([*compose, *args])
                if done.returncode != 0:
                    raise ConfigError(f"docker compose {args[0]} failed: {done.stderr[-300:]}")
            clone = opts.data_dir / "repo"
            config = load_factory_config(clone / ".factory")
            quality: QualityCommands = config.quality
            report = await selftest(
                quality, known_failing_fixture(clone), env=settings.subprocess_base_env()
            )
            assert_self_test(report)
            log.append({"kind": "upgrade", "step": "self_test", "ok": report.ok})
            log.append(
                {"kind": "upgrade", "step": "eval", "status": "eval replay joins in phase 4"}
            )
        except (FactoryError, OSError) as exc:
            warn(f"upgrade to {opts.pin} failed ({exc}); rolling back to {current}")
            rollback = commands(["git", "-C", str(opts.factory_root), "checkout", "-q", current])
            restart = commands([*compose, "up", "-d", "--build", "--remove-orphans"])
            writer.set_admissions_paused(False)
            writer.write_event(
                EventKind.CONTROL,
                who,
                payload={
                    "action": "factory_upgrade",
                    "status": "rolled_back",
                    "to": opts.pin,
                    "error": str(exc)[:300],
                    "rollback_ok": rollback.returncode == 0 and restart.returncode == 0,
                },
            )
            log.append(
                {"kind": "upgrade", "step": "rollback", "to": current, "error": str(exc)[:300]}
            )
            return 1
        writer.set_factory_version(opts.pin)
        writer.set_admissions_paused(False)
        seq = writer.write_event(
            EventKind.CONTROL,
            who,
            payload={
                "action": "factory_upgrade",
                "status": "done",
                "from": current,
                "to": opts.pin,
            },
        )
        log.append({"kind": "upgrade", "step": "done", "version": opts.pin, "event_seq": seq})
        say(f"factory upgraded {current} → {opts.pin} (event {seq}); admissions resumed")
        return 0
    finally:
        db.close()


def upgrade(
    opts: UpgradeOptions,
    *,
    commands: Commands = run_command,
    selftest: SelfTester = self_test,
) -> int:
    return asyncio.run(upgrade_async(opts, commands=commands, selftest=selftest))
