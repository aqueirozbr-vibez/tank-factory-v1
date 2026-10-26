"""Unit 37: the internal network, the nftables rules for the VM, and the denial tailer."""

from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from orchestrator.budget.keys import PhaseKeys
from orchestrator.models.enums import AgentRole
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceWriter
from orchestrator.watchdogs import EgressTailer, nftables_rules, parse_denial
from orchestrator.workspace.container import CliResult, ContainerError, ensure_network
from tests.unit.test_container import FakeDocker
from tests.unit.test_proxy import FakeLiteLLM
from tests.unit.test_spec_gate import TASK

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = (
    "[ 8123.447] factory-egress IN=br-1a2b3c4d5e6f OUT=eth0 PHYSIN=veth9f MAC=02:42:ac:12:00:05"
    " SRC=10.77.0.5 DST=140.82.112.3 LEN=60 TOS=0x00 PREC=0x00 TTL=63 ID=12345 DF PROTO=TCP"
    " SPT=43210 DPT=443 WINDOW=64240 RES=0x00 SYN URGP=0"
)


class NetworkFakeDocker(FakeDocker):
    def __init__(self, *, existing: dict[str, bool] | None = None) -> None:
        super().__init__()
        self.networks: dict[str, bool] = dict(existing or {})
        self.by_ip: dict[str, tuple[str, str]] = {}

    async def _run(self, args: Sequence[str], *, timeout_s: float = 300) -> CliResult:
        if args[0] == "network":
            self.calls.append(list(args))
            if args[1] == "inspect":
                name = args[-1]
                if name not in self.networks:
                    raise ContainerError("no such network")
                return CliResult(0, "true" if self.networks[name] else "false", "")
            if args[1] == "create":
                self.networks[args[-1]] = "--internal" in args
                return CliResult(0, "netid", "")
        return await super()._run(args, timeout_s=timeout_s)

    async def task_containers_by_ip(self, network: str) -> dict[str, tuple[str, str]]:
        return dict(self.by_ip)


async def test_ensure_network_creates_internal_and_refuses_open() -> None:
    docker = NetworkFakeDocker()
    assert await ensure_network(docker, "factory-tasks") == "factory-tasks"
    create = next(c for c in docker.calls if c[:2] == ["network", "create"])
    assert "--internal" in create and create[-1] == "factory-tasks"
    assert await ensure_network(docker, "factory-tasks") == "factory-tasks"  # idempotent
    assert sum(1 for c in docker.calls if c[:2] == ["network", "create"]) == 1
    with pytest.raises(ContainerError, match="internal"):
        await ensure_network(NetworkFakeDocker(existing={"factory-tasks": False}), "factory-tasks")


def test_nftables_rules_and_shipped_example() -> None:
    rules = nftables_rules("br-1a2b3c4d5e6f", "10.77.0.2")
    assert "table inet factory" in rules and 'iifname "br-1a2b3c4d5e6f"' in rules
    assert "ct state established,related accept" in rules
    assert "ip daddr 10.77.0.2 tcp dport 4000 accept" in rules
    assert 'log prefix "factory-egress " level warn' in rules and rules.rstrip().endswith("}")
    assert rules.index("accept") < rules.index("log prefix") < rules.index("drop")
    example = (ROOT / "deploy" / "egress" / "factory-tasks.nft.example").read_text(encoding="utf-8")
    assert example == nftables_rules("br-factorytasks0", "172.30.0.2")


def test_parse_denial_reads_the_kernel_line() -> None:
    denial = parse_denial(SAMPLE)
    assert denial is not None
    assert (denial.src, denial.dst, denial.proto, denial.dport) == (
        "10.77.0.5",
        "140.82.112.3",
        "TCP",
        443,
    )
    assert parse_denial("kernel: audit: type=1400 apparmor") is None
    assert (
        parse_denial("factory-egress IN=br-x OUT=eth0 PROTO=ICMP SRC=10.77.0.9 DST=1.1.1.1")
        is not None
    )


async def test_tailer_writes_egress_denied_with_the_task(trace_writer: TraceWriter) -> None:
    docker = NetworkFakeDocker()
    docker.by_ip["10.77.0.5"] = ("factory-task-418-1", TASK)
    from tests.unit.test_build_phase import make_config

    cfg = make_config()
    trace_writer.load_config_version(**cfg.config_version_row())
    trace_writer.insert_task(
        task_id=TASK,
        issue_number=418,
        title="t",
        branch="agent/418",
        workspace_kind="container",  # type: ignore[arg-type]
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
    )
    tailer = EgressTailer(trace_writer, docker, network="factory-tasks")
    assert await tailer.feed(SAMPLE) is True
    assert await tailer.feed("unrelated kernel line") is False
    assert await tailer.feed(SAMPLE.replace("10.77.0.5", "10.77.0.77")) is True  # unknown source
    rows = trace_writer.db.fetchall(
        "SELECT task_id, payload_json FROM events WHERE kind = 'egress_denied' ORDER BY seq"
    )
    assert len(rows) == 2 and rows[0]["task_id"] == TASK and rows[1]["task_id"] is None
    assert '"dst":"140.82.112.3"' in rows[0]["payload_json"].replace(" ", "")
    assert '"container":"factory-task-418-1"' in rows[0]["payload_json"].replace(" ", "")
    assert tailer.denials == 2


async def test_task_env_points_at_the_proxy_alias(tmp_path: Path) -> None:
    fake = FakeLiteLLM()
    keys = PhaseKeys(fake.admin(), task_proxy_url="http://proxy:4000")
    issued = await keys.issue(
        settings=Settings(data_dir=tmp_path / "data"),
        task_id=TASK,
        phase_id="p",
        role=AgentRole.BUILDER,
        harness="claude_code",
        model="m",
        cap_usd=1.0,
        timeout_s=60,
    )
    assert issued.env["ANTHROPIC_BASE_URL"] == "http://proxy:4000"
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.proxy.yml").read_text(encoding="utf-8")
    )
    assert compose["networks"]["factory-tasks"] == {"external": True}
    assert compose["services"]["litellm"]["networks"]["factory-tasks"] == {"aliases": ["proxy"]}
    assert (
        Settings().docker_network == "factory-tasks"
        and Settings().proxy_url_in_tasks == "http://proxy:4000"
    )
