"""Unit 44: the deployment bundle files, the installer's steps and the upgrade procedure."""

import base64
import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cli.install import (
    COMPOSE_FILE,
    InstallOptions,
    UpgradeOptions,
    ensure_export_key,
    install,
    proxy_env,
    render_env,
    upgrade,
)
from cli.seed import seed_repository
from orchestrator.budget.dry_run import InstallLog
from orchestrator.errors import ConfigError
from orchestrator.gates.base import Check
from orchestrator.gates.selftest import SelfTestReport
from orchestrator.intake.scheduler import Scheduler
from orchestrator.models.config import QualityCommands
from orchestrator.models.enums import TaskStatus, WorkspaceKind
from orchestrator.supervisor import Supervisor
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.version import factory_version
from orchestrator.workspace.git import run_git
from tests.conftest import DEFAULT_VENDORS
from tests.unit.test_build_phase import TASK_ID

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


class Commands:
    """Records every host command; one substring can be made to fail."""

    def __init__(self, failing: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.failing = failing

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        joined = " ".join(argv)
        code = 1 if self.failing is not None and self.failing in joined else 0
        return subprocess.CompletedProcess(list(argv), code, "", "boom" if code else "")

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


async def ok_selftest(
    commands: QualityCommands, fixture_dir: Path, *, env: object, **_: object
) -> SelfTestReport:
    return SelfTestReport(True, (Check("format", True, "exit 1"),))


def app_key(tmp_path: Path) -> Path:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    target = tmp_path / "app.pem"
    target.write_text(pem, encoding="utf-8")
    return target


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("install") / "demo"
    seed_repository(target)
    return target


def test_bundle_files_are_consistent() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"orchestrator", "litellm", "litellm-db", "caddy"}
    orchestrator = compose["services"]["orchestrator"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in orchestrator["volumes"]
    assert any("/etc/factory" in v and v.endswith(":ro") for v in orchestrator["volumes"])
    assert orchestrator["env_file"] == ["${FACTORY_ETC_DIR:-/etc/factory}/factory.env"]
    assert "ANTHROPIC_API_KEY" not in json.dumps(orchestrator)
    assert orchestrator["ports"] == ["127.0.0.1:${FACTORY_API_PORT:-8080}:8080"]
    litellm = compose["services"]["litellm"]
    assert litellm["networks"]["factory-tasks"] == {"aliases": ["proxy"]}
    assert litellm["env_file"] == ["${FACTORY_ETC_DIR:-/etc/factory}/proxy.env"]
    assert compose["networks"]["factory-tasks"] == {"external": True}
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "tls internal" in caddy and "handle /webhooks/github" in caddy
    assert "respond 404" in caddy and "reverse_proxy orchestrator:8080" in caddy
    unit = (DEPLOY / "factory.service").read_text(encoding="utf-8")
    assert "docker compose -f /opt/factory/deploy/docker-compose.yml" in unit and "up -d" in unit
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in dockerfile and "docker:27-cli" in dockerfile
    entrypoint = (DEPLOY / "entrypoint.sh").read_text(encoding="utf-8")
    assert entrypoint.startswith("#!/bin/sh") and "exec factory" in entrypoint
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".venv" in ignored and ".env" in ignored and "tests" in ignored


def test_env_rendering_keys_and_export_key(tmp_path: Path) -> None:
    assert render_env({"B": "2", "A": "1"}) == "A=1\nB=2\n"
    with pytest.raises(ConfigError):
        render_env({"A": "x\ny"})
    first = ensure_export_key(tmp_path / "etc")
    second = ensure_export_key(tmp_path / "etc")
    assert first == second and len(base64.b64decode(first)) == 32
    pem = (tmp_path / "etc" / "export.key").read_text(encoding="utf-8")
    assert pem.startswith("-----BEGIN PRIVATE KEY-----")
    values, present = proxy_env("sk-m", {"ANTHROPIC_API_KEY": "sk-ant", "OTHER": "x"})
    assert values == {"LITELLM_MASTER_KEY": "sk-m", "ANTHROPIC_API_KEY": "sk-ant"}
    assert present == ["ANTHROPIC_API_KEY"]


def test_install_with_the_fake_harness(
    seeded: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pem_file = app_key(tmp_path)
    monkeypatch.setenv("FACTORY_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("FACTORY_GITHUB_INSTALLATION_ID", "77")
    monkeypatch.setenv("FACTORY_GITHUB_PRIVATE_KEY_FILE", str(pem_file))
    monkeypatch.delenv("FACTORY_PROXY_MASTER_KEY", raising=False)
    etc, data = tmp_path / "etc", tmp_path / "data"
    opts = InstallOptions(
        repo_clone=seeded,
        org="dev",
        repo="demo",
        harness="fake",
        scripts_dir=data / "repo" / ".factory-dev" / "scripts",
        etc_dir=etc,
        data_dir=data,
        issues=(6, 7, 8),
        skip_image=True,
        skip_protection=True,
        vendor_env={"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": "sk-oa-test"},
    )
    commands = Commands()
    code = install(opts, commands=commands, probe=lambda _url: True)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err

    assert (etc / "github-app.pem").read_text(encoding="utf-8") == pem_file.read_text(
        encoding="utf-8"
    )
    env = dict(
        line.split("=", 1)
        for line in (etc / "factory.env").read_text(encoding="utf-8").splitlines()
    )
    assert env["FACTORY_WORKSPACE"] == "container" and env["FACTORY_HARNESS"] == "fake"
    assert env["FACTORY_PROXY_MASTER_KEY"].startswith("sk-")
    assert env["FACTORY_REPO_CLONE"] == str((data / "repo").resolve())
    assert env["FACTORY_PROXY_URL"] == "http://litellm:4000"
    assert "ANTHROPIC_API_KEY" not in env and "FACTORY_TASK_IMAGE" not in env
    proxy = dict(
        line.split("=", 1) for line in (etc / "proxy.env").read_text(encoding="utf-8").splitlines()
    )
    assert proxy["ANTHROPIC_API_KEY"] == "sk-ant-test" and proxy["OPENAI_API_KEY"] == "sk-oa-test"
    assert proxy["LITELLM_MASTER_KEY"] == env["FACTORY_PROXY_MASTER_KEY"]

    assert (data / "hooks" / "factory_pretooluse.py").is_file()
    assert not (data / "hooks" / "__pycache__").exists()
    clone = data / "repo"
    assert (clone / ".factory" / "roster.yaml").is_file()
    assert run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=clone).stdout.strip() == "main"
    template = run_git(
        ["show", "factory-install:.github/ISSUE_TEMPLATE/factory-task.yml"], cwd=clone
    ).stdout
    assert "Factory task" in template

    db = TraceDB.open(data / "trace.db")
    try:
        installation = TraceWriter(db).get_installation()
        assert installation is not None
        assert installation["factory_version"] == factory_version()
        assert len(base64.b64decode(installation["export_pubkey"])) == 32
        assert (
            installation["per_task_budget_usd"] == 5.0
        )  # the floor; 2x issue 8's 0.38 sits below it
        assert installation["budget_mode"] == "install"
    finally:
        db.close()
    records = InstallLog(data).records()
    steps = [r["step"] for r in records if r["kind"] == "install"]
    assert steps == [
        "etc",
        "data",
        "trace",
        "product_repo",
        "self_test",
        "env",
        "compose",
        "healthz",
        "done",
    ]
    assert any(r["kind"] == "dry_run" and not r["refused"] for r in records)
    done = next(r for r in records if r.get("step") == "done")
    assert done["export_pubkey"] == installation["export_pubkey"]
    assert commands.calls[0][:3] == ["docker", "compose", "-f"] and "--build" in commands.calls[0]
    assert "export public key" in captured.out

    again = install(opts, commands=Commands(), probe=lambda _url: True)
    assert again == 0  # idempotent: the export key and the installation row are kept
    assert ensure_export_key(etc) == installation["export_pubkey"]


def test_install_preflight_refusals(
    seeded: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("FACTORY_GITHUB_APP_ID", raising=False)
    opts = InstallOptions(
        repo_clone=seeded, org="dev", repo="demo", etc_dir=tmp_path / "e", data_dir=tmp_path / "d"
    )
    assert install(opts) == 2 and "FACTORY_GITHUB_APP_ID" in capsys.readouterr().err
    monkeypatch.setenv("FACTORY_GITHUB_APP_ID", "1")
    monkeypatch.setenv("FACTORY_GITHUB_INSTALLATION_ID", "2")
    monkeypatch.setenv("FACTORY_GITHUB_PRIVATE_KEY_FILE", str(app_key(tmp_path)))
    short = InstallOptions(
        repo_clone=seeded,
        org="dev",
        repo="demo",
        etc_dir=tmp_path / "e",
        data_dir=tmp_path / "d",
        issues=(1,),
    )
    assert install(short) == 2 and "exactly 3" in capsys.readouterr().err
    bare = InstallOptions(repo_clone=tmp_path, org="dev", repo="demo", skip_dry_run=True)
    assert install(bare) == 2 and ".factory" in capsys.readouterr().err


def seeded_trace(data: Path, version: str = "0.1.0") -> None:
    (data / "repo" / ".factory").mkdir(parents=True)
    for file in (ROOT / "fixtures" / "factory_config").iterdir():
        shutil.copy2(file, data / "repo" / ".factory" / file.name)
    db = TraceDB.open(data / "trace.db")
    try:
        writer = TraceWriter(db)
        writer.load_config_version(
            config_sha="cfg1",
            main_commit="abc",
            roster_json="{}",
            risk_yaml="",
            review_policy_yaml="",
            protected_paths_json="[]",
            quality_commands_json="{}",
            vendors_json=DEFAULT_VENDORS,
        )
        writer.insert_installation(
            org="acme",
            repo="atlas-api",
            factory_version=version,
            config_sha="cfg1",
            per_task_budget_usd=6.0,
            export_pubkey="pk",
        )
    finally:
        db.close()


def installation_state(data: Path) -> tuple[str, int, list[dict[str, object]]]:
    db = TraceDB.open(data / "trace.db")
    try:
        row = TraceWriter(db).get_installation()
        assert row is not None
        controls = [
            json.loads(r["payload_json"])
            for r in db.fetchall(
                "SELECT payload_json FROM events WHERE kind = 'control' ORDER BY seq"
            )
        ]
        return str(row["factory_version"]), int(row["admissions_paused"]), controls
    finally:
        db.close()


def test_upgrade_noop_then_procedure_then_rollback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, etc = tmp_path / "data", tmp_path / "etc"
    seeded_trace(data)

    def options(pin: str) -> UpgradeOptions:
        return UpgradeOptions(
            pin=pin, reason="release", actor="ops", factory_root=ROOT, etc_dir=etc, data_dir=data
        )

    commands = Commands()
    assert upgrade(options("0.1.0"), commands=commands, selftest=ok_selftest) == 0
    assert commands.calls == [] and "already at 0.1.0" in capsys.readouterr().out

    assert upgrade(options("0.2.0"), commands=commands, selftest=ok_selftest) == 0
    joined = commands.joined()
    assert any(c.endswith("fetch --tags origin") for c in joined)
    assert any(c.endswith("checkout -q 0.2.0") for c in joined)
    assert any(" pull" in c for c in joined) and any("up -d --build" in c for c in joined)
    version, paused, controls = installation_state(data)
    assert version == "0.2.0" and paused == 0
    assert [c.get("status", "started") for c in controls] == ["started", "done"]
    assert controls[0]["from"] == "0.1.0" and controls[0]["reason"] == "release"
    steps = [r["step"] for r in InstallLog(data).records() if r["kind"] == "upgrade"]
    assert steps == ["pause", "drain", "migrations", "self_test", "eval", "done"]

    failing = Commands(failing="up -d --build")
    assert upgrade(options("0.3.0"), commands=failing, selftest=ok_selftest) == 1
    version, paused, controls = installation_state(data)
    assert version == "0.2.0" and paused == 0
    assert controls[-1]["status"] == "rolled_back" and controls[-1]["to"] == "0.3.0"
    assert any(c.endswith("checkout -q 0.2.0") for c in failing.joined()[-2:])
    assert "rolling back to 0.2.0" in capsys.readouterr().err


async def test_scheduler_holds_while_admissions_are_paused(trace_writer: TraceWriter) -> None:
    trace_writer.insert_task(
        task_id=TASK_ID,
        issue_number=418,
        title="t",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
        status=TaskStatus.QUEUED,
    )

    async def noop(_task_id: str) -> None:
        return None

    scheduler = Scheduler(trace_writer, Supervisor(noop))
    trace_writer.set_admissions_paused(True)
    assert trace_writer.admissions_paused() and await scheduler.dispatch() == []
    trace_writer.set_admissions_paused(False)
    assert await scheduler.dispatch() == [TASK_ID]
