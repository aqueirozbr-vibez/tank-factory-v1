"""Unit 35: per-phase virtual keys against a fake LiteLLM; the agent env holds no vendor key."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml

from orchestrator.budget.keys import PROXY_ENV_KEYS, PhaseKeys, agent_env_for
from orchestrator.budget.proxy import ProxyAdmin, ProxyError, litellm_config, looks_like_cap
from orchestrator.config import load_factory_config
from orchestrator.harness.base import HarnessResult, Usage
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.models.enums import AgentRole
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceWriter
from tests.unit.test_build_phase import GREEN, SPEC_REL, TASK_ID, deps_for, worktree_for

ROOT = Path(__file__).resolve().parents[2]
PROXY = "http://proxy.test:4000"
MASTER = "sk-master"


class FakeLiteLLM:
    """Enough of the LiteLLM admin API for keys: generate, info, delete, liveliness."""

    def __init__(self, *, default_spend: float = 0.0) -> None:
        self.keys: dict[str, dict[str, object]] = {}
        self.deleted: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self.default_spend = default_spend
        self.issued = 0

    def set_spend(self, key: str, spend: float) -> None:
        self.keys[key]["spend"] = spend

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.headers.get("authorization") != f"Bearer {MASTER}":
            return httpx.Response(401, json={"error": "invalid master key"})
        path = request.url.path
        if path == "/health/liveliness":
            return httpx.Response(200, text="I'm alive!")
        if path == "/key/generate":
            body = json.loads(request.content)
            self.issued += 1
            number = self.issued
            key = f"sk-vk-{number}"
            self.keys[key] = {**body, "spend": self.default_spend, "token": f"hash-{number}"}
            return httpx.Response(
                200, json={"key": key, "token": f"hash-{number}", "expires": "2026-10-25T00:00:00Z"}
            )
        if path == "/key/info":
            key = request.url.params.get("key", "")
            info = self.keys.get(key)
            if info is None:
                return httpx.Response(404, json={"error": "key not found"})
            return httpx.Response(200, json={"key": key, "info": info})
        if path == "/key/delete":
            keys = json.loads(request.content)["keys"]
            for key in keys:
                self.keys.pop(key, None)
            self.deleted.extend(keys)
            return httpx.Response(200, json={"deleted_keys": keys})
        return httpx.Response(404, json={"error": "unknown route"})

    def admin(self, master: str = MASTER) -> ProxyAdmin:
        return ProxyAdmin(
            PROXY, master, http=httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        )


def result(
    *, completed: bool = True, cost: float = 0.41, error: str | None = None
) -> HarnessResult:
    return HarnessResult(
        harness="fake",
        harness_version="fake-0.1",
        outcome="completed" if completed else "error",
        envelope_text="{}" if completed else None,
        session_id="s",
        usage=Usage(input_tokens=10, output_tokens=2, cost_usd=cost),
        exit_code=0 if completed else 1,
        error=error,
    )


async def test_create_info_delete_roundtrip() -> None:
    fake = FakeLiteLLM()
    admin = fake.admin()
    assert await admin.health()
    minted = await admin.create_key(
        task_id="acme/atlas-api#418",
        phase_id="ph-1",
        role="builder",
        max_budget_usd=3.0,
        duration_s=7200,
        models=["claude-opus-5-5"],
    )
    assert minted.key == "sk-vk-1" and minted.key_id == "hash-1" and minted.expires
    stored = fake.keys["sk-vk-1"]
    assert stored["max_budget"] == 3.0 and stored["duration"] == "7200s"  # grace is PhaseKeys'
    assert stored["models"] == ["claude-opus-5-5"]
    assert stored["metadata"] == {
        "task_id": "acme/atlas-api#418",
        "phase_id": "ph-1",
        "role": "builder",
    }
    fake.set_spend("sk-vk-1", 0.37)
    info = await admin.key_info("sk-vk-1")
    assert info.spend == 0.37 and info.max_budget == 3.0 and not info.blocked
    assert await admin.delete_key("sk-vk-1") and fake.deleted == ["sk-vk-1"]
    assert await admin.delete_key("sk-vk-1") is True  # the fake deletes idempotently
    with pytest.raises(ProxyError, match="401"):
        await fake.admin("sk-wrong").create_key(
            task_id="t", phase_id="p", role="planner", max_budget_usd=1.0, duration_s=60, models=[]
        )
    assert not await fake.admin("sk-wrong").health()
    assert (
        looks_like_cap("Budget has been exceeded")
        and looks_like_cap("HTTP 429")
        and not looks_like_cap(None)
    )


async def test_issued_env_holds_only_the_virtual_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-vendor-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-vendor-secret")
    monkeypatch.setenv("CODEX_API_KEY", "sk-codex-vendor-secret")
    settings = Settings(data_dir=tmp_path / "data")
    fake = FakeLiteLLM()
    keys = PhaseKeys(fake.admin())
    issued = await keys.issue(
        settings=settings,
        task_id=TASK_ID,
        phase_id="ph-7",
        role=AgentRole.BUILDER,
        harness="claude_code",
        model="claude-opus-5-5",
        cap_usd=3.0,
        timeout_s=7200,
    )
    assert issued.proxied and issued.key == "sk-vk-1" and issued.key_id == "hash-1"
    env = issued.env
    assert env["ANTHROPIC_API_KEY"] == "sk-vk-1" and env["ANTHROPIC_BASE_URL"] == PROXY
    assert (
        env["FACTORY_PHASE_ID"] == "ph-7" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    )
    assert "OPENAI_API_KEY" not in env and "CODEX_API_KEY" not in env
    assert all("vendor-secret" not in value for value in env.values())

    codex_env = agent_env_for(
        settings, harness="codex", key="sk-vk-9", proxy_url=PROXY, phase_id="ph-8"
    )
    assert codex_env["OPENAI_BASE_URL"] == f"{PROXY}/v1"
    assert codex_env["OPENAI_API_KEY"] == codex_env["CODEX_API_KEY"] == "sk-vk-9"
    assert codex_env["CODEX_HOME"] == str(tmp_path / "data" / "codex-home")
    assert "ANTHROPIC_API_KEY" not in codex_env
    assert set(codex_env) & PROXY_ENV_KEYS == {
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_HOME",
        "FACTORY_PHASE_ID",
    }

    plain = await PhaseKeys().issue(
        settings=settings,
        task_id=TASK_ID,
        phase_id="ph-9",
        role=AgentRole.BUILDER,
        harness="claude_code",
        model="m",
        cap_usd=1.0,
        timeout_s=60,
    )
    assert not plain.proxied and plain.env == settings.agent_env()  # phase 1 pass-through


async def test_settle_meters_from_the_proxy_and_revokes(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    fake = FakeLiteLLM()
    keys = PhaseKeys(fake.admin())
    issued = await keys.issue(
        settings=settings,
        task_id=TASK_ID,
        phase_id="p1",
        role=AgentRole.REVIEWER,
        harness="codex",
        model="gpt-5-codex",
        cap_usd=1.5,
        timeout_s=2700,
    )
    fake.set_spend("sk-vk-1", 0.37)
    settled = await keys.settle(issued, result(cost=0.41))
    assert settled.completed and settled.usage.cost_usd == 0.37 and settled.usage.input_tokens == 10
    assert fake.deleted == ["sk-vk-1"]

    capped = await keys.issue(
        settings=settings,
        task_id=TASK_ID,
        phase_id="p2",
        role=AgentRole.BUILDER,
        harness="claude_code",
        model="claude-opus-5-5",
        cap_usd=3.0,
        timeout_s=60,
    )
    fake.set_spend("sk-vk-2", 3.0)
    stopped = await keys.settle(capped, result(completed=False, cost=0.0, error="API error 400"))
    assert stopped.error is not None and stopped.error.startswith("error_max_budget_usd")
    assert stopped.usage.cost_usd == 3.0 and fake.deleted == ["sk-vk-1", "sk-vk-2"]

    worded = await keys.issue(
        settings=settings,
        task_id=TASK_ID,
        phase_id="p3",
        role=AgentRole.BUILDER,
        harness="claude_code",
        model="claude-opus-5-5",
        cap_usd=3.0,
        timeout_s=60,
    )
    fake.set_spend("sk-vk-3", 2.9)
    stopped = await keys.settle(worded, result(completed=False, error="Budget has been exceeded"))
    assert stopped.error is not None and stopped.error.startswith("error_max_budget_usd")

    unchanged = await PhaseKeys().settle(
        plain_issue := await PhaseKeys().issue(
            settings=settings,
            task_id=TASK_ID,
            phase_id="p4",
            role=AgentRole.BUILDER,
            harness="claude_code",
            model="m",
            cap_usd=1.0,
            timeout_s=60,
        ),
        result(cost=0.41),
    )
    assert not plain_issue.proxied and unchanged.usage.cost_usd == 0.41


async def test_build_phase_uses_the_phase_key(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    fake = FakeLiteLLM(default_spend=0.05)
    deps, harness, _ = deps_for(trace_writer, [GREEN])
    deps = replace(deps, keys=PhaseKeys(fake.admin()))
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed
    build_row = trace_writer.db.fetchone(
        "SELECT virtual_key_id, cost_usd FROM phases WHERE name = 'build'"
    )
    assert build_row is not None and build_row["virtual_key_id"] == "hash-1"
    assert build_row["cost_usd"] == pytest.approx(0.05)
    assert harness.calls[0].env["ANTHROPIC_API_KEY"] == "sk-vk-1"
    assert harness.calls[0].env["ANTHROPIC_BASE_URL"] == PROXY
    assert fake.deleted == ["sk-vk-1"] and fake.keys == {}
    start = trace_writer.db.fetchone("SELECT payload_json FROM events WHERE kind = 'agent_start'")
    assert start is not None and '"virtual_key_id":"hash-1"' in start["payload_json"].replace(
        " ", ""
    )
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["spent_usd"] == pytest.approx(0.05)


def test_litellm_config_and_compose_match_the_shipped_files() -> None:
    cfg = load_factory_config(ROOT / "fixtures" / "factory_config")
    rendered = litellm_config(cfg.roster)
    assert rendered == (ROOT / "deploy" / "litellm" / "config.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(rendered)
    names = {m["model_name"]: m["litellm_params"]["model"] for m in parsed["model_list"]}
    assert names == {
        "claude-opus-5-5": "anthropic/claude-opus-5-5",
        "gpt-5-codex": "openai/gpt-5-codex",
    }
    assert all(
        m["litellm_params"]["api_key"].startswith("os.environ/") for m in parsed["model_list"]
    )
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.proxy.yml").read_text(encoding="utf-8")
    )
    litellm = compose["services"]["litellm"]
    assert litellm["ports"] == ["127.0.0.1:${FACTORY_PROXY_PORT:-4000}:4000"]
    assert "./litellm/config.yaml:/config/config.yaml:ro" in litellm["volumes"]
    assert "DATABASE_URL" in litellm["environment"] and "litellm-db" in compose["services"]
