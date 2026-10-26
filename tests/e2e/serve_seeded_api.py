"""Serve the orchestrator API over a seeded trace for the console's Playwright and load checks.

Usage: uv run python tests/e2e/serve_seeded_api.py <data_dir> [port]

The trace holds the perf seeder's 1,000 tasks (unit 59: Overview under 2 s p95), three running
tasks (``acme/atlas-api#7`` with a full phase history, a gate ladder failing at ``lint``, three
envelopes, a risk breakdown, a review and 10,000 ``tool_call`` rows; ``#17`` and ``#27`` with a
build each) that gain one row every two seconds (units 60 and 66: the live tail and its lag), a
plan waiting for approval (``#8``), the shipped ``.factory/`` files as two configuration
versions, an eval set of 24 items with a baseline run and a blocked ``config_pr`` run (units
62 to 64), risk classes on the merged tasks so the monthly audit queue fills, a kill switch
without a supervisor, and a webhook door that trusts loopback with the secret
``e2e-webhook-secret`` and admits ``factory:ready`` labels into ``tasks`` (unit 66: admission
latency). Loopback callers are accepted without a service token, as the dev door allows, and
every signed-in e2e login counts as ``write`` for the control endpoints.
"""

import asyncio
import json
import pathlib
import sys
from datetime import UTC, datetime, timedelta

import uvicorn
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # the app root

from orchestrator import constants
from orchestrator.api.app import ApiDeps, create_app
from orchestrator.api.controls import ControlDeps
from orchestrator.intake.webhook import GitHubIpRanges
from orchestrator.models.enums import (
    AgentRole,
    EventKind,
    GateName,
    OutputType,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    TaskStatus,
    WorkspaceKind,
)
from orchestrator.models.events import NormalisedEvent
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.trace.kpi import KpiMaterialiser
from orchestrator.watchdogs import KillSwitch
from tests.conftest import DEFAULT_VENDORS
from tests.integration.test_api_perf import seed

LIVE_TASK = "acme/atlas-api#7"
LIVE_TASKS = (LIVE_TASK, "acme/atlas-api#17", "acme/atlas-api#27")  # all seeded as running
PLAN_TASK = "acme/atlas-api#8"
TRACE_ROWS = 10_000
SMALL_ROWS = 50
APPEND_EVERY_S = 2.0
WEBHOOK_SECRET = "e2e-webhook-secret"
EVAL_CLASSES = ("bug", "test", "docs", "feature")

TOOLS = ("Read", "Grep", "Edit", "Bash", "Write")
TIMES = chr(0xD7)  # the multiplication sign the risk instrument prints
CONFIG_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "factory_config"


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def risk_payload() -> dict[str, object]:
    base, factor = 40.0, 1.3
    score = base * factor
    return {
        "tier": "B",
        "tier_name": "service logic",
        "base": base,
        "factors": [
            {
                "name": "files touched",
                "rule": f"6 to 10 files in Scope: {TIMES}1.2",
                "value": factor,
            },
            {"name": "dependencies", "rule": f"no new dependency: {TIMES}1.0", "value": 1.0},
        ],
        "score": score,
        "risk": "risk:high",
        "equation": f"{base:g} {TIMES} {factor:g} {TIMES} 1 = {score:g}",
        "conclusion": (
            f"{score:g} >= {constants.RISK_HIGH_THRESHOLD:g}: plan approval before build."
        ),
        "reasons": [],
        "scope_paths": ["src/billing/invoice.py", "tests/test_invoice.py"],
        "governed_path": None,
        "overridden_by": None,
        "override_reason": None,
        "instrument_version": 1,
    }


def verdict_payload() -> dict[str, object]:
    """One scored review the Review findings screen renders (PRD Capability 4)."""
    major = {
        "requirement_id": "AC3",
        "severity": "Major",
        "tier": "Major",
        "location": "src/billing/invoice.py:41",
        "problem": "Zero payment terms give the wrong due date.",
        "equation": f"70 {TIMES} 1 {TIMES} 0.8 {TIMES} 1 = 56",
        "score": 56.0,
        "base": 70.0,
        "spec_link": 1.0,
        "evidence": 0.8,
        "scope": 1.0,
        "dismissed_by": None,
        "in_diff": True,
        "notes": [],
    }
    minor = {
        **major,
        "requirement_id": "perf",
        "severity": "Minor",
        "tier": "Minor",
        "location": None,
        "problem": "The due-date loop recomputes the calendar per invoice.",
        "equation": f"30 {TIMES} 0.5 {TIMES} 0.4 {TIMES} 1 = 6",
        "score": 6.0,
        "base": 30.0,
        "spec_link": 0.5,
        "evidence": 0.4,
        "in_diff": None,
    }
    dismissed = {
        **major,
        "requirement_id": "performance",
        "problem": "No caching of invoice queries.",
        "equation": f"70 {TIMES} 1 {TIMES} 0.8 {TIMES} 0 = 0",
        "score": 0.0,
        "scope": 0.0,
        "dismissed_by": "Caching of invoice queries.",
        "notes": ["dismissed by Non-goals: Caching of invoice queries."],
    }
    return {
        "policy_version": 1,
        "verdict": "revision_needed",
        "reviewer_verdict": "accepted",
        "conformance": 44.0,
        "blocking_score": 56.0,
        "strongest": major,
        "findings": [major, minor],
        "dismissed": [dismissed],
        "human_reasons": [],
        "missing_ac": [],
        "revisions_so_far": 0,
        "overridden_by": None,
        "override_reason": None,
        "accept_conformance": 70.0,
    }


class Seeder:
    def __init__(self, writer: TraceWriter) -> None:
        self.writer = writer
        self.now = datetime.now(UTC)

    def event(self, kind: EventKind, actor: str, task_id: str, **fields: object) -> int:
        return self.writer.write_event(kind, actor, task_id=task_id, **fields)  # type: ignore[arg-type]

    def phase(
        self,
        task_id: str,
        run_id: str,
        seq: int,
        name: PhaseName,
        kind: PhaseKind,
        owner: str,
        status: PhaseStatus | None,
        *,
        cost: float = 0.0,
        harness: str | None = None,
        error: str | None = None,
    ) -> str:
        phase_id = self.writer.start_phase(
            run_id=run_id,
            seq=seq,
            name=name,
            kind=kind,
            owner=owner,
            timeout_s=1800,
            harness=harness,
            model_id="model-a" if harness else None,
        )
        self.event(
            EventKind.PHASE_START,
            "orchestrator",
            task_id,
            run_id=run_id,
            phase_id=phase_id,
            payload={"name": str(name), "attempt": 1},
        )
        if status is not None:
            self.writer.finish_phase(phase_id, status, cost_usd=cost, error=error)
            self.event(
                EventKind.PHASE_END,
                "orchestrator",
                task_id,
                run_id=run_id,
                phase_id=phase_id,
                payload={"status": str(status), "error": error},
            )
        return phase_id

    def tool_rows(self, task_id: str, run_id: str, phase_id: str, count: int, actor: str) -> None:
        start = self.now - timedelta(hours=1)
        step = timedelta(hours=1) / max(count, 1)
        for n in range(count):
            started = start + step * n
            tool = TOOLS[n % len(TOOLS)]
            self.event(
                EventKind.TOOL_CALL,
                actor,
                task_id,
                run_id=run_id,
                phase_id=phase_id,
                payload={
                    "tool": tool,
                    "tool_call_id": f"call_{n}",
                    "args": {"path": f"src/billing/module_{n % 40}.py"}
                    if tool != "Bash"
                    else {"command": "uv run pytest -q tests/test_invoice.py"},
                    "result_snippet": f"{tool} ok ({n})",
                    "ok": n % 97 != 0,
                    "duration_ms": 40 + (n * 7) % 900,
                    "denied": False,
                    "agent": actor,
                },
                started_at=iso(started),
                ended_at=iso(started + timedelta(milliseconds=40 + (n * 7) % 900)),
            )

    def live_task(self) -> dict[str, str]:
        """``#7``: every surface the Task detail and Review findings render."""
        w = self.writer
        w.set_task_risk(LIVE_TASK, risk="risk:high", score=52.0)
        w.set_task_spec(LIVE_TASK, spec_path="docs/specs/7-invoice-rounding.md", spec_sha="b" * 40)
        run_id = w.start_run(
            task_id=LIVE_TASK, attempt=1, factory_version="0.1.0", config_sha="cfg1"
        )
        p = self.phase
        plan = p(
            LIVE_TASK,
            run_id,
            1,
            PhaseName.PLAN,
            PhaseKind.AGENT,
            "planner",
            PhaseStatus.PASS,
            cost=0.82,
            harness="claude_code",
        )
        spec_gate = p(
            LIVE_TASK,
            run_id,
            2,
            PhaseName.SPEC_GATE,
            PhaseKind.CODE,
            "orchestrator",
            PhaseStatus.PASS,
        )
        self.event(
            EventKind.LOG,
            "orchestrator",
            LIVE_TASK,
            run_id=run_id,
            phase_id=spec_gate,
            payload={"risk": risk_payload()},
        )
        p(LIVE_TASK, run_id, 3, PhaseName.RISK, PhaseKind.CODE, "orchestrator", PhaseStatus.PASS)
        p(
            LIVE_TASK,
            run_id,
            4,
            PhaseName.PLAN_APPROVAL,
            PhaseKind.ENGINEER,
            "tech_lead",
            PhaseStatus.PASS,
        )
        build = p(
            LIVE_TASK,
            run_id,
            5,
            PhaseName.BUILD,
            PhaseKind.AGENT,
            "builder",
            PhaseStatus.PASS,
            cost=1.93,
            harness="codex",
        )
        gates_1 = p(
            LIVE_TASK,
            run_id,
            6,
            PhaseName.CODE_GATES,
            PhaseKind.CODE,
            "orchestrator",
            PhaseStatus.FAIL,
            error="lint",
        )
        gates_2 = p(
            LIVE_TASK,
            run_id,
            7,
            PhaseName.CODE_GATES,
            PhaseKind.CODE,
            "orchestrator",
            PhaseStatus.PASS,
        )
        review = p(
            LIVE_TASK,
            run_id,
            8,
            PhaseName.REVIEW,
            PhaseKind.AGENT,
            "reviewer",
            None,
            harness="claude_code",
        )

        for gate in (
            GateName.SPEC_SECTIONS,
            GateName.SPEC_EARS,
            GateName.SPEC_AC_HOLDOUT_MAP,
            GateName.SPEC_ADR_CITED,
        ):
            w.write_gate_result(
                run_id=run_id,
                phase_id=spec_gate,
                attempt=1,
                gate=gate,
                passed=True,
                checks=[{"item": str(gate), "ok": True, "note": ""}],
            )
        w.write_gate_result(
            run_id=run_id,
            phase_id=gates_1,
            attempt=1,
            gate=GateName.FORMAT,
            passed=True,
            checks=[
                {"item": "ruff format --check", "ok": True, "note": "0 files would be reformatted"}
            ],
            command="uv run ruff format --check .",
            exit_code=0,
            output_tail="",
        )
        w.write_gate_result(
            run_id=run_id,
            phase_id=gates_1,
            attempt=1,
            gate=GateName.LINT,
            passed=False,
            checks=[{"item": "ruff check", "ok": False, "note": "1 error"}],
            violations=[
                {
                    "item": "src/billing/invoice.py:41",
                    "problem": "F841 local variable `total` is assigned to but never used",
                }
            ],
            command="uv run ruff check .",
            exit_code=1,
            output_tail=(
                "src/billing/invoice.py:41:5: F841 Local variable `total` is assigned to"
                " but never used\n"
                "Found 1 error.\n"
            ),
        )
        for gate in (GateName.FORMAT, GateName.LINT, GateName.TYPECHECK, GateName.FOCUSED_TESTS):
            w.write_gate_result(
                run_id=run_id,
                phase_id=gates_2,
                attempt=1,
                gate=gate,
                passed=True,
                checks=[{"item": str(gate), "ok": True, "note": ""}],
                command=f"uv run {gate}",
                exit_code=0,
                output_tail="ok\n",
            )

        w.write_envelope(
            run_id=run_id,
            phase_id=plan,
            agent=AgentRole.PLANNER,
            output_type=OutputType.PLAN,
            payload_json=json.dumps(
                {
                    "output_type": "PlanOutput",
                    "summary": "Round invoice totals half-up at two decimals.",
                    "spec_path": "docs/specs/7-invoice-rounding.md",
                    "risk_inputs": {"tiers_touched": ["B"], "files_touched": 7},
                    "open_questions": [],
                },
                indent=2,
            ),
            valid=True,
            attempt=1,
        )
        w.write_envelope(
            run_id=run_id,
            phase_id=build,
            agent=AgentRole.BUILDER,
            output_type=OutputType.BUILD,
            payload_json=(
                '{"output_type": "BuildOutput", "summary": "rounded totals",'
                ' "files_changed": [{"path": "src/billing/invoice.py"}]}'
            ),
            valid=False,
            attempt=1,
            violations=[{"item": "files_changed.0.change", "problem": "Field required"}],
        )
        w.write_envelope(
            run_id=run_id,
            phase_id=build,
            agent=AgentRole.BUILDER,
            output_type=OutputType.BUILD,
            payload_json=json.dumps(
                {
                    "output_type": "BuildOutput",
                    "summary": "rounded totals half-up; focused tests added",
                    "files_changed": [
                        {"path": "src/billing/invoice.py", "change": "round half-up"},
                        {"path": "tests/test_invoice.py", "change": "3 cases"},
                    ],
                    "tests_run": ["tests/test_invoice.py"],
                },
                indent=2,
            ),
            valid=True,
            attempt=2,
        )
        w.write_envelope(
            run_id=run_id,
            phase_id=review,
            agent=AgentRole.REVIEWER,
            output_type=OutputType.REVIEW,
            payload_json=json.dumps(
                {
                    "output_type": "ReviewOutput",
                    "approved": False,
                    "verdict": "accepted",
                    "findings": [
                        {
                            "requirement_id": "AC1",
                            "met": True,
                            "severity": "Nit",
                            "problem": "fine",
                            "evidence": (
                                "tests/acceptance/test_invoice_holdout.py::test_due_date passes"
                            ),
                        },
                        {
                            "requirement_id": "AC3",
                            "met": False,
                            "severity": "Major",
                            "problem": "Zero payment terms give the wrong due date.",
                            "file": "src/billing/invoice.py",
                            "line": 41,
                            "evidence": "",
                        },
                    ],
                    "blocking": ["AC3"],
                },
                indent=2,
            ),
            valid=True,
            attempt=1,
        )
        self.event(
            EventKind.LOG,
            "orchestrator",
            LIVE_TASK,
            run_id=run_id,
            phase_id=review,
            payload={"verdict_policy": verdict_payload()},
        )
        self.tool_rows(LIVE_TASK, run_id, build, TRACE_ROWS, "builder")
        return {"run_id": run_id, "phase_id": review}

    def small_live_task(self, task_id: str) -> dict[str, str]:
        """``#17`` and ``#27``: a running build so three tails run at once (unit 66 SSE lag)."""
        run_id = self.writer.start_run(
            task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
        )
        build = self.phase(
            task_id, run_id, 1, PhaseName.BUILD, PhaseKind.AGENT, "builder", None, harness="codex"
        )
        self.tool_rows(task_id, run_id, build, SMALL_ROWS, "builder")
        return {"run_id": run_id, "phase_id": build}

    def plan_waiting(self) -> None:
        """``#8``: a risk:high plan waiting on ``factory:plan-approved`` (the Approvals queue)."""
        w = self.writer
        w.set_task_status(PLAN_TASK, TaskStatus.RUNNING)
        w.set_task_risk(PLAN_TASK, risk="risk:high", score=52.0)
        w.set_task_spec(PLAN_TASK, spec_path="docs/specs/8-late-fee.md", spec_sha="c" * 40)
        run_id = w.start_run(
            task_id=PLAN_TASK, attempt=1, factory_version="0.1.0", config_sha="cfg1"
        )
        p = self.phase
        p(
            PLAN_TASK,
            run_id,
            1,
            PhaseName.PLAN,
            PhaseKind.AGENT,
            "planner",
            PhaseStatus.PASS,
            cost=0.7,
            harness="claude_code",
        )
        spec_gate = p(
            PLAN_TASK,
            run_id,
            2,
            PhaseName.SPEC_GATE,
            PhaseKind.CODE,
            "orchestrator",
            PhaseStatus.PASS,
        )
        self.event(
            EventKind.LOG,
            "orchestrator",
            PLAN_TASK,
            run_id=run_id,
            phase_id=spec_gate,
            payload={"risk": risk_payload()},
        )
        w.write_gate_result(
            run_id=run_id,
            phase_id=spec_gate,
            attempt=1,
            gate=GateName.SPEC_AC_HOLDOUT_MAP,
            passed=True,
            checks=[
                {
                    "item": "AC1",
                    "ok": True,
                    "note": "tests/acceptance/test_late_fee_holdout.py::test_fee_applied",
                },
                {
                    "item": "AC2",
                    "ok": True,
                    "note": "tests/acceptance/test_late_fee_holdout.py::test_fee_cap",
                },
            ],
        )
        p(PLAN_TASK, run_id, 3, PhaseName.RISK, PhaseKind.CODE, "orchestrator", PhaseStatus.PASS)
        p(PLAN_TASK, run_id, 4, PhaseName.PLAN_APPROVAL, PhaseKind.ENGINEER, "tech_lead", None)

    def audit_strata(self) -> None:
        """Risk classes on the merged tasks so the monthly audit draws every stratum."""
        classes = ("risk:low", "risk:high", "risk:governed")
        for n in range(1, 1001):
            if n % 10 < 3:  # the perf seeder's merged bucket
                self.writer.set_task_risk(
                    f"acme/atlas-api#{n}", risk=classes[n % 3], score=float(20 + (n % 3) * 30)
                )

    def eval_set(self) -> None:
        """24 issues in the fixed class mix, a baseline run and a blocked ``config_pr`` run."""
        w = self.writer
        set_id = w.create_eval_set(name="atlas-api eval", repo="acme/atlas-api")
        items = [
            w.add_eval_item(set_id, issue_number=100 + i, eval_class=EVAL_CLASSES[i % 4])
            for i in range(1, 25)
        ]

        def run(
            config_sha: str,
            trigger: str,
            trigger_ref: str | None,
            baseline: str | None,
            passing: int,
        ) -> str:
            run_id = w.start_eval_run(
                set_id=set_id,
                factory_version="0.1.0",
                config_sha=config_sha,
                trigger=trigger,
                trigger_ref=trigger_ref,
                baseline_run_id=baseline,
            )
            per_class: dict[str, list[int]] = {c: [0, 0] for c in EVAL_CLASSES}
            for i, item_id in enumerate(items):
                passed = i < passing
                cls = EVAL_CLASSES[(i + 1) % 4]
                per_class[cls][1] += 1
                per_class[cls][0] += 1 if passed else 0
                w.record_eval_result(
                    run_id,
                    item_id=item_id,
                    passed=passed,
                    cost_usd=0.35,
                    duration_s=90,
                    failure_reason=None if passed else "holdout failed",
                )
            rate = round(100 * passing / len(items), 2)
            rates = {c: round(100 * ok / total, 2) for c, (ok, total) in per_class.items()}
            delta = None if baseline is None else round(rate - 62.5, 2)
            blocked = None if delta is None else delta < -constants.EVAL_BLOCK_DROP_POINTS
            w.finish_eval_run(
                run_id,
                status="complete",
                pass_rate=rate,
                per_class_json=json.dumps(rates, sort_keys=True),
                delta_points=delta,
                blocked=blocked,
            )
            return run_id

        baseline = run("cfg0", "manual", None, None, 15)
        run("cfg1", "config_pr", "52", baseline, 11)


def load_shipped_config(writer: TraceWriter) -> None:
    """The shipped ``.factory/`` files as two configuration versions (cfg0, then cfg1)."""
    roster = yaml.safe_load((CONFIG_DIR / "roster.yaml").read_text(encoding="utf-8"))
    quality = yaml.safe_load((CONFIG_DIR / "quality.yaml").read_text(encoding="utf-8"))
    factory = yaml.safe_load((CONFIG_DIR / "factory.yaml").read_text(encoding="utf-8")) or {}
    protected = list(constants.PROTECTED_PATHS_DEFAULT) + list(factory.get("protected_paths") or [])
    for sha, commit in (("cfg0", "a" * 40), ("cfg1", "b" * 40)):
        writer.load_config_version(
            config_sha=sha,
            main_commit=commit,
            roster_json=json.dumps(roster),
            risk_yaml=(CONFIG_DIR / "risk.yaml").read_text(encoding="utf-8"),
            review_policy_yaml=(CONFIG_DIR / "review-policy.yaml").read_text(encoding="utf-8"),
            protected_paths_json=json.dumps(protected),
            quality_commands_json=json.dumps(quality),
            vendors_json=DEFAULT_VENDORS,
        )


async def e2e_permission(_login: str) -> str | None:
    """The seeded API has no GitHub: every signed-in e2e login counts as write."""
    return "write"


def main() -> None:
    data_dir = pathlib.Path(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    data_dir.mkdir(parents=True, exist_ok=True)
    db = TraceDB.open(data_dir / "trace.db")
    writer = TraceWriter(db)
    if writer.get_installation() is None:
        load_shipped_config(writer)
        writer.insert_installation(
            org="acme",
            repo="atlas-api",
            factory_version="0.1.0",
            config_sha="cfg1",
            per_task_budget_usd=6.0,
            export_pubkey="pk",
        )
        seed(writer)
        seeder = Seeder(writer)
        seeder.audit_strata()
        live = {LIVE_TASK: seeder.live_task()}
        for task_id in LIVE_TASKS[1:]:
            live[task_id] = seeder.small_live_task(task_id)
        seeder.plan_waiting()
        seeder.eval_set()
        (data_dir / "live.json").write_text(json.dumps(live), encoding="utf-8")
        KpiMaterialiser(writer, clock=lambda: datetime.now(UTC)).materialise()
    live = json.loads((data_dir / "live.json").read_text(encoding="utf-8"))
    settings = Settings(data_dir=data_dir, webhook_secret=WEBHOOK_SECRET)
    deps = ApiDeps(
        writer=writer,
        settings=settings,
        ip_ranges=GitHubIpRanges((), datetime.now(UTC), trust_loopback=True),
    )
    deps.controls = ControlDeps(
        writer=writer,
        settings=settings,
        limiter=deps.limiter,
        permission_for=e2e_permission,
        kill_switch=KillSwitch(writer),  # no supervisor here: the flag, the rows, the event
    )
    app = create_app(deps)

    async def append_rows() -> None:
        n = 0
        while True:
            await asyncio.sleep(APPEND_EVERY_S)
            n += 1
            moment = datetime.now(UTC)
            for task_id, ids in live.items():
                actor = "reviewer" if task_id == LIVE_TASK else "builder"
                writer.write_event(
                    EventKind.TOOL_CALL,
                    actor,
                    task_id=task_id,
                    run_id=ids["run_id"],
                    phase_id=ids["phase_id"],
                    payload={
                        "tool": "Read",
                        "tool_call_id": f"live_{n}",
                        "args": {"path": "src/billing/invoice.py"},
                        "result_snippet": f"live row {n}",
                        "ok": True,
                        "duration_ms": 55,
                        "denied": False,
                        "agent": actor,
                    },
                    started_at=iso(moment),
                    ended_at=iso(moment),
                )

    async def admit_labels() -> None:
        """A stand-in for the intake's admission: a ``factory:ready`` label becomes a task row."""
        installation = writer.get_installation()
        budget = float(installation["per_task_budget_usd"]) if installation else 6.0
        while True:
            event: NormalisedEvent = await deps.inbox.get()
            if (
                event.kind == "issue_labeled"
                and event.label == constants.LABEL_READY
                and event.issue_number
            ):
                writer.insert_task(
                    task_id=f"acme/atlas-api#{event.issue_number}",
                    issue_number=event.issue_number,
                    title=f"load issue {event.issue_number}",
                    branch=f"agent/{event.issue_number}",
                    workspace_kind=WorkspaceKind.WORKTREE,
                    budget_usd=budget,
                    admitted_by=event.actor_login or "webhook",
                    config_sha="cfg1",
                )

    background: list[asyncio.Task[None]] = []

    @app.on_event("startup")
    async def start_background() -> None:
        background.append(asyncio.create_task(append_rows()))
        background.append(asyncio.create_task(admit_labels()))

    tasks = db.scalar("SELECT COUNT(*) FROM tasks")
    rows = db.scalar("SELECT COUNT(*) FROM events WHERE task_id = ?", (LIVE_TASK,))
    print(
        f"seeded API on http://127.0.0.1:{port} with {tasks} tasks and {rows} rows on {LIVE_TASK}",
        flush=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
