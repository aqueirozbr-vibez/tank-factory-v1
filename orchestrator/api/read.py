"""The read endpoints of ``/api/v1`` (TRD §6.1): trace rows out, nothing computed per request.

Every handler identifies the caller, applies the rule's rate limit, reads
through the trace writer and returns a pydantic model. Lists page by an
opaque cursor at ``PAGE_LIMIT`` rows at most. The KPI and indicator reads
serve the materialised ``kpi_daily`` rows (unit 48), never a live sum.
"""

import base64
import json
import re
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from orchestrator.api.limits import Caller, RateLimiter, error, identify, limited
from orchestrator.api.streams import changes_stream, task_events_stream
from orchestrator.budget.check import day_start_utc
from orchestrator.intake.issue_fetcher import detect_language
from orchestrator.models.api import (
    ApiModel,
    ApprovalView,
    AuditQueue,
    AuditQueueItem,
    BudgetView,
    ChainFooter,
    ConfigHistory,
    ConfigHistoryItem,
    ConfigView,
    DryRunNote,
    EnvelopeList,
    EnvelopeView,
    ErrorBody,
    EvalResultView,
    EvalRunDetail,
    EvalRunList,
    EvalRunView,
    EvalSetList,
    EvalSetView,
    EventPage,
    EventView,
    GateList,
    GateView,
    IndicatorResponse,
    InstallationView,
    KillSwitchNote,
    KpiResponse,
    LinksView,
    MetricView,
    NeedsHumanCount,
    PhaseView,
    PlanQueue,
    PlanQueueItem,
    QuestionItem,
    QuestionQueue,
    ReviewView,
    RunView,
    TaskDetail,
    TaskPage,
    TaskSummary,
)
from orchestrator.models.enums import TaskStatus
from orchestrator.models.settings import Settings
from orchestrator.trace.chain import ChainReport, verify_chain
from orchestrator.trace.db import TraceDB
from orchestrator.trace.kpi import HEADLINE, local_day, local_month
from orchestrator.trace.retention import mask_payload, pseudonymise, purged_logins
from orchestrator.trace.writer import TraceWriter

PAGE_LIMIT = 200
ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorBody, "description": text}
    for code, text in (
        (400, "a parameter is malformed"),
        (403, "the caller is unknown"),
        (404, "no such task or run"),
        (409, "nothing to show yet"),
        (429, "rate limited; the rule is named"),
        (503, "the installation row is missing"),
    )
}
STREAM_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"description": "server-sent events", "content": {"text/event-stream": {}}},
    **ERRORS,
}
TASK_STATUSES = frozenset(str(s) for s in TaskStatus)
QUESTION_LINE = re.compile(r"^- (.+)$", re.MULTILINE)
DECISION_REASONS = frozenset(
    {
        "spec_gate",
        "missing_holdout",
        "protected_path",
        "repair_cap",
        "revision_cap",
        "human_decision_needed",
        "timeout",
        "dependency_change",
        "full_suite",
    }
)
# PRD §Metrics targets and the leading-indicator lines (TRD §9), by series name
KPI_TARGETS: dict[str, str] = {
    "factory_merge_share": ">= 30%",
    "merge_rate": ">= 70%",
    "unattended_rate": ">= 80%",
    "audit_disagreement_low": "<= 5%",
    "audit_disagreement_high": "reported beside, not pooled",
    "audit_disagreement_governed": "reported beside, not pooled",
    "cost_per_merged_pr": "<= 2x the 30-day median",
    "cost_per_merged_pr_median_30d": "the median itself",
    "over_budget_count": "0",
}
INDICATOR_THRESHOLDS: dict[str, dict[str, float]] = {
    "repair_rounds_mean": {"target": 1.5, "review": 2.5},
    "escalation_rate": {"target": 20.0, "review": 35.0},
    "review_minutes_median": {"target": 20.0, "review": 40.0},
    "eval_pass_rate": {"target": 60.0, "class_floor": 40.0},
    "eval_gap_points": {"floor": -10.0},
    "protected_path_attempts": {"target": 0.0},
    "stuck_runs_7d": {"target": 0.0, "review": 1.0},
    "verdict_override_rate": {"review": 20.0},
}
GitShow = Callable[[Path, str, str], str | None]


# --- helpers --------------------------------------------------------------------------------------


def _json(text: object) -> object | None:
    return None if text is None else json.loads(str(text))


def from_row[M: ApiModel](model: type[M], row: sqlite3.Row, **overrides: object) -> M:
    """Pick the model's fields from a row; JSON columns and bools come through ``overrides``."""
    keys = set(row.keys())
    values: dict[str, object] = {
        name: row[name] for name in model.model_fields if name in keys and name not in overrides
    }
    values.update(overrides)
    return model.model_validate(values)


def encode_cursor(*parts: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> list[object] | None:
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return None
    return parts if isinstance(parts, list) else None


def parse_limit(raw: str | None) -> int | None:
    if raw is None:
        return PAGE_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= PAGE_LIMIT else None


def git_show(clone: Path, ref: str, path: str) -> str | None:
    """A committed file's text from the clone (the spec on the task branch), or ``None``."""
    try:
        result = subprocess.run(  # fixed argv, no shell
            ["git", "show", f"{ref}:{path}"],
            cwd=clone,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def parse_questions(evidence: str) -> list[str]:
    return [m.group(1).strip() for m in QUESTION_LINE.finditer(evidence)]


class ReadDeps:
    """What the read router needs: the writer, settings, the limiter, the clone for spec text."""

    def __init__(
        self,
        writer: TraceWriter,
        settings: Settings,
        limiter: RateLimiter,
        *,
        repo_clone: Path | None = None,
        show: GitShow = git_show,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.writer = writer
        self.settings = settings
        self.limiter = limiter
        self.repo_clone = repo_clone
        self.show = show
        self.clock = clock or (lambda: datetime.now(UTC))

    def guard(self, request: Request, rule: str) -> tuple[Caller, JSONResponse | None]:
        caller = identify(request, self.settings)
        if not caller.known:
            return caller, error(
                403,
                "caller",
                "reads take the console's service token with X-Factory-Actor, or a loopback caller",
            )
        if not self.limiter.allow(caller.key, rule):
            return caller, limited(rule, caller)
        return caller, None

    # --- row views --------------------------------------------------------------------------------

    def installation(self) -> sqlite3.Row | None:
        return self.writer.get_installation()

    def timezone(self) -> str:
        row = self.installation()
        return str(row["timezone"]) if row is not None else "UTC"

    def repo_url(self) -> str | None:
        row = self.installation()
        return None if row is None else f"https://github.com/{row['org']}/{row['repo']}"

    def task_summary(self, row: sqlite3.Row) -> TaskSummary:
        return from_row(TaskSummary, row)

    def event_view(self, row: sqlite3.Row, purged: frozenset[str]) -> EventView:
        payload = _json(row["payload_json"])
        return from_row(
            EventView,
            row,
            actor=pseudonymise(str(row["actor"]), purged),
            payload=mask_payload(payload, purged) if purged else payload,
        )

    def envelope_view(self, row: sqlite3.Row) -> EnvelopeView:
        return from_row(
            EnvelopeView,
            row,
            payload=_json(row["payload_json"]),
            violations=_json(row["violations_json"]),
            valid=bool(row["valid"]),
        )

    def gate_view(self, row: sqlite3.Row) -> GateView:
        return from_row(
            GateView,
            row,
            checks=_json(row["checks_json"]),
            violations=_json(row["violations_json"]),
            passed=bool(row["passed"]),
        )

    def eval_run_view(self, row: sqlite3.Row) -> EvalRunView:
        blocked = row["blocked"]
        return from_row(
            EvalRunView,
            row,
            per_class=_json(row["per_class_json"]),
            blocked=None if blocked is None else bool(blocked),
        )

    def links(self, task: sqlite3.Row) -> LinksView:
        base = self.repo_url() or ""
        spec = task["spec_path"]
        return LinksView(
            issue=f"{base}/issues/{task['issue_number']}",
            branch=f"{base}/tree/{task['branch']}",
            spec=None if not spec else f"{base}/blob/{task['branch']}/{spec}",
            pr=None if task["pr_number"] is None else f"{base}/pull/{task['pr_number']}",
        )

    def metrics(
        self, names: Sequence[str] | None, *, kind: str
    ) -> tuple[str | None, list[MetricView]]:
        """The newest ``kpi_daily`` day and its rows (headline series or indicators)."""
        day = self.writer.db.scalar("SELECT MAX(day) FROM kpi_daily")
        if day is None:
            return None, []
        rows = self.writer.kpis_for_day(str(day))
        out: list[MetricView] = []
        for name, row in rows.items():
            headline = name in KPI_TARGETS or any(name.startswith(h) for h in HEADLINE)
            if (kind == "kpi") != headline:
                continue
            if names is not None and name not in names:
                continue
            thresholds = INDICATOR_THRESHOLDS.get(name)
            if thresholds is None and name.startswith("eval_pass_rate_"):
                thresholds = {"class_floor": 40.0}
            out.append(
                MetricView(
                    metric=name,
                    value=row["value"],
                    numerator=row["numerator"],
                    denominator=row["denominator"],
                    target=KPI_TARGETS.get(name) if kind == "kpi" else None,
                    thresholds=thresholds if kind == "indicator" else None,
                )
            )
        return str(day), out


# --- the router -----------------------------------------------------------------------------------


def read_router(deps: ReadDeps) -> APIRouter:  # one handler per endpoint
    router = APIRouter()
    writer = deps.writer
    db = writer.db

    @router.get("/installation", response_model=InstallationView, responses=ERRORS)
    async def installation(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        row = deps.installation()
        if row is None:
            return error(503, "installation", "the installation row is missing")
        since = day_start_utc(deps.clock(), str(row["timezone"]))
        return from_row(
            InstallationView,
            row,
            factory_enabled=bool(row["factory_enabled"]),
            admissions_paused=bool(row["admissions_paused"]),
            spent_today_usd=round(writer.spent_since(since), 6),
            open_incident_seq=open_incident_seq(db),
            last_kill_switch=last_kill_switch(db),
            last_dry_run=last_dry_run(db),
        )

    @router.get("/tasks", response_model=TaskPage, responses=ERRORS)
    async def tasks(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        params = request.query_params
        status = params.get("status")
        if status is not None and status not in TASK_STATUSES:
            return error(400, "tasks_status", f"status must be one of {sorted(TASK_STATUSES)}")
        limit = parse_limit(params.get("limit"))
        if limit is None:
            return error(400, "tasks_limit", f"limit must be an integer from 1 to {PAGE_LIMIT}")
        clauses: list[str] = []
        args: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            args.append(status)
        cursor = params.get("cursor")
        if cursor is not None:
            parts = decode_cursor(cursor)
            if parts is None or len(parts) != 2:
                return error(400, "tasks_cursor", "cursor is not one this API issued")
            clauses.append("(created_at < ? OR (created_at = ? AND task_id > ?))")
            args.extend([parts[0], parts[0], parts[1]])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.fetchall(
            f"SELECT * FROM tasks{where} ORDER BY created_at DESC, task_id LIMIT ?",
            (*args, limit + 1),
        )
        page = rows[:limit]
        next_cursor = (
            encode_cursor(page[-1]["created_at"], page[-1]["task_id"])
            if len(rows) > limit
            else None
        )
        return TaskPage(items=[deps.task_summary(r) for r in page], next=next_cursor)

    @router.get("/tasks/{task_id:path}/events/stream", responses=STREAM_RESPONSES)
    async def task_events(task_id: str, request: Request) -> object:
        caller = identify(request, deps.settings)
        if not caller.known:
            return error(
                403, "caller", "streams take the console's service token or a loopback caller"
            )
        if writer.get_task(task_id) is None:
            return error(404, "task", f"no task {task_id}")
        after = request.query_params.get("after", "0")
        if not after.isdigit():
            return error(400, "stream_after", "after must be an event seq")
        if not deps.limiter.open_stream(caller.key):
            return limited("read", caller)  # the stream cap: 5 per login
        generator = task_events_stream(
            writer,
            task_id,
            after=int(after),
            purged=purged_logins(writer),
            on_close=lambda: deps.limiter.close_stream(caller.key),
        )
        return StreamingResponse(generator, media_type="text/event-stream")

    @router.get("/tasks/{task_id:path}/events", response_model=EventPage, responses=ERRORS)
    async def task_event_page(task_id: str, request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        if writer.get_task(task_id) is None:
            return error(404, "task", f"no task {task_id}")
        params = request.query_params
        limit = parse_limit(params.get("limit"))
        after = params.get("after", "0")
        before = params.get("before")
        bad_before = before is not None and not before.isdigit()
        if limit is None or not after.isdigit() or bad_before:
            return error(
                400, "events_page", f"after and before are seqs and limit is 1 to {PAGE_LIMIT}"
            )
        kind = params.get("kind")
        # `before` pages backwards from the head, newest first (TRD §3.4: the first paint
        # shows the last page); `after` pages forwards, oldest first.
        backwards = before is not None
        clauses = ["task_id = ?", "seq < ?" if backwards else "seq > ?"]
        args: list[object] = [task_id, int(before) if before is not None else int(after)]
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        order = "DESC" if backwards else "ASC"
        rows = db.fetchall(
            f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY seq {order} LIMIT ?",
            (*args, limit + 1),
        )
        page = rows[:limit]
        purged = purged_logins(writer)
        items = [deps.event_view(r, purged) for r in page]
        return EventPage(items=items, next=str(page[-1]["seq"]) if len(rows) > limit else None)

    @router.get("/tasks/{task_id:path}/envelopes", response_model=EnvelopeList, responses=ERRORS)
    async def task_envelopes(task_id: str, request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        if writer.get_task(task_id) is None:
            return error(404, "task", f"no task {task_id}")
        rows = db.fetchall(
            "SELECT e.* FROM envelopes e JOIN runs r ON r.run_id = e.run_id"
            " WHERE r.task_id = ? ORDER BY e.created_at, e.rowid",
            (task_id,),
        )
        return EnvelopeList(items=[deps.envelope_view(r) for r in rows])

    @router.get("/tasks/{task_id:path}/gates", response_model=GateList, responses=ERRORS)
    async def task_gates(task_id: str, request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        if writer.get_task(task_id) is None:
            return error(404, "task", f"no task {task_id}")
        rows = db.fetchall(
            "SELECT g.* FROM gate_results g JOIN runs r ON r.run_id = g.run_id"
            " WHERE r.task_id = ? ORDER BY g.created_at, g.rowid",
            (task_id,),
        )
        return GateList(items=[deps.gate_view(r) for r in rows])

    @router.get("/tasks/{task_id:path}/review", response_model=ReviewView, responses=ERRORS)
    async def task_review(task_id: str, request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        if writer.get_task(task_id) is None:
            return error(404, "task", f"no task {task_id}")
        rows = db.fetchall(
            "SELECT seq, phase_id, payload_json FROM events WHERE task_id = ? AND kind = 'log'"
            " AND payload_json LIKE ? ORDER BY seq",
            (task_id, '%"verdict_policy":%'),
        )
        if not rows:
            return error(409, "review_pending", f"{task_id} has no review yet")
        wanted = request.query_params.get("round")
        index = len(rows) - 1
        if wanted is not None:
            if not wanted.isdigit() or not 1 <= int(wanted) <= len(rows):
                return error(400, "review_round", f"round is 1 to {len(rows)}")
            index = int(wanted) - 1
        row = rows[index]
        score = json.loads(row["payload_json"])["verdict_policy"]
        envelope = db.scalar(
            "SELECT payload_json FROM envelopes WHERE phase_id = ? AND output_type = 'ReviewOutput'"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (row["phase_id"],),
        )
        return ReviewView(
            round=index + 1,
            rounds=len(rows),
            phase_id=row["phase_id"],
            verdict=str(score.get("verdict")),
            reviewer_verdict=score.get("reviewer_verdict"),
            conformance=score.get("conformance"),
            blocking_score=score.get("blocking_score"),
            strongest=score.get("strongest"),
            findings=list(score.get("findings") or []),
            dismissed=list(score.get("dismissed") or []),
            human_reasons=[str(r) for r in score.get("human_reasons") or []],
            missing_ac=[str(a) for a in score.get("missing_ac") or []],
            policy_version=score.get("policy_version"),
            accept_conformance=score.get("accept_conformance"),
            overridden_by=score.get("overridden_by"),
            override_reason=score.get("override_reason"),
            envelope=_json(envelope),
        )

    @router.get("/tasks/{task_id:path}", response_model=TaskDetail, responses=ERRORS)
    async def task_detail(task_id: str, request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        task = writer.get_task(task_id)
        if task is None:
            return error(404, "task", f"no task {task_id}")
        runs = db.fetchall("SELECT * FROM runs WHERE task_id = ? ORDER BY attempt", (task_id,))
        phases = db.fetchall(
            "SELECT p.* FROM phases p JOIN runs r ON r.run_id = p.run_id WHERE r.task_id = ?"
            " ORDER BY r.attempt, p.seq",
            (task_id,),
        )
        approvals = db.fetchall(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at", (task_id,)
        )
        bounds = db.fetchone("SELECT MIN(seq), MAX(seq) FROM events WHERE task_id = ?", (task_id,))
        if bounds is not None and bounds[0] is not None:
            report = chain_report(db, task_id, max(1, int(bounds[0]) - 1), int(bounds[1]) + 1)
            footer = ChainFooter(
                verified=report.ok,
                records=report.records,
                head_hash=report.head_hash,
                first_bad_seq=report.first_bad_seq,
                head_seq=int(bounds[1]),
            )
        else:
            footer = ChainFooter(verified=True, records=0, head_hash=None)
        budget = float(task["budget_usd"])
        spent = float(task["spent_usd"])
        return TaskDetail(
            task=deps.task_summary(task),
            runs=[from_row(RunView, r) for r in runs],
            phases=[from_row(PhaseView, p) for p in phases],
            approvals=[
                from_row(
                    ApprovalView,
                    a,
                    codeowner=None if a["codeowner"] is None else bool(a["codeowner"]),
                )
                for a in approvals
            ],
            budget=BudgetView(
                budget_usd=budget,
                spent_usd=spent,
                remaining_usd=round(max(0.0, budget - spent), 6),
                raised=writer.count_controls(task_id, "budget_raise") > 0,
                last_stop=writer.latest_budget_stop(task_id),
            ),
            links=deps.links(task),
            chain=footer,
        )

    @router.get("/approvals/plans", response_model=PlanQueue, responses=ERRORS)
    async def approvals_plans(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        rows = db.fetchall(
            "SELECT t.*, p.phase_id AS waiting_phase, p.started_at AS waiting_since, r.run_id"
            " FROM phases p JOIN runs r ON r.run_id = p.run_id"
            " JOIN tasks t ON t.task_id = r.task_id"
            " WHERE p.name = 'plan_approval' AND p.status = 'running' ORDER BY p.started_at"
        )
        items: list[PlanQueueItem] = []
        for row in rows:
            spec_path = row["spec_path"]
            spec_text = None
            if deps.repo_clone is not None and spec_path:
                spec_text = deps.show(deps.repo_clone, str(row["branch"]), str(spec_path))
            gate = db.fetchone(
                "SELECT checks_json FROM gate_results WHERE run_id = ?"
                " AND gate = 'spec_ac_holdout_map'"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (row["run_id"],),
            )
            checks = _json(gate["checks_json"]) if gate is not None else None
            items.append(
                PlanQueueItem(
                    task_id=str(row["task_id"]),
                    issue_number=int(row["issue_number"]),
                    title=row["title"],
                    risk=row["risk"],
                    risk_score=row["risk_score"],
                    governed_path=row["governed_path"],
                    spec_path=spec_path,
                    spec_sha=row["spec_sha"],
                    spec_text=spec_text,
                    risk_breakdown=writer.latest_log_payload(str(row["run_id"]), "risk"),
                    ac_holdout=list(checks) if isinstance(checks, list) else [],
                    waiting_since=str(row["waiting_since"]),
                    phase_id=str(row["waiting_phase"]),
                )
            )
        return PlanQueue(items=items)

    @router.get("/approvals/questions", response_model=QuestionQueue, responses=ERRORS)
    async def approvals_questions(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        rows = db.fetchall(
            "SELECT * FROM tasks WHERE status = 'blocked' AND blocked_reason = 'open_questions'"
            " ORDER BY updated_at"
        )
        items: list[QuestionItem] = []
        for row in rows:
            event = db.fetchone(
                "SELECT payload_json FROM events WHERE task_id = ? AND kind = 'error'"
                " AND payload_json LIKE ? ORDER BY seq DESC LIMIT 1",
                (row["task_id"], '%"blocked_reason":"open_questions"%'),
            )
            evidence = ""
            if event is not None and event["payload_json"] is not None:
                evidence = str(json.loads(event["payload_json"]).get("evidence") or "")
            questions = parse_questions(evidence)
            items.append(
                QuestionItem(
                    task_id=str(row["task_id"]),
                    issue_number=int(row["issue_number"]),
                    title=row["title"],
                    questions=questions,
                    language=detect_language("\n".join(questions) or str(row["title"] or "")),
                    blocked_at=str(row["updated_at"]),
                )
            )
        return QuestionQueue(items=items)

    @router.get("/audit/queue", response_model=AuditQueue, responses=ERRORS)
    async def audit_queue(request: Request) -> object:
        _caller, refused = deps.guard(request, "audit")
        if refused is not None:
            return refused
        month = request.query_params.get("month") or local_month(deps.clock(), deps.timezone())
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            return error(400, "audit_month", "month is YYYY-MM")
        rows = db.fetchall(
            "SELECT * FROM v_audit_queue WHERE month = ? ORDER BY stratum, task_id", (month,)
        )
        return AuditQueue(
            items=[
                AuditQueueItem(
                    id=str(r["id"]),
                    task_id=str(r["task_id"]),
                    pr=int(r["pr"]),
                    month=str(r["month"]),
                    stratum=str(r["stratum"]),
                    risk=r["risk"],
                    sampled=bool(r["sampled"]),
                )
                for r in rows
            ]
        )

    @router.get("/kpis", response_model=KpiResponse, responses=ERRORS)
    async def kpis(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        window = request.query_params.get("window", "30d")
        if window != "30d":
            return error(400, "kpis_window", "window is 30d (the PRD's window)")
        day, items = deps.metrics(None, kind="kpi")
        return KpiResponse(day=day, window=window, items=items)

    @router.get("/indicators", response_model=IndicatorResponse, responses=ERRORS)
    async def indicators(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        day, items = deps.metrics(None, kind="indicator")
        return IndicatorResponse(day=day, items=items)

    @router.get("/needs-human/count", response_model=NeedsHumanCount, responses=ERRORS)
    async def needs_human_count(request: Request) -> object:
        _caller, refused = deps.guard(request, "count")
        if refused is not None:
            return refused
        plans = int(
            db.scalar(
                "SELECT COUNT(*) FROM phases WHERE name = 'plan_approval' AND status = 'running'"
            )
            or 0
        )
        by_reason = {
            str(r["blocked_reason"]): int(r["n"])
            for r in db.fetchall(
                "SELECT blocked_reason, COUNT(*) AS n FROM tasks WHERE status = 'blocked'"
                " GROUP BY blocked_reason"
            )
        }
        return NeedsHumanCount(
            plans=plans,
            questions=by_reason.get("open_questions", 0),
            decisions=sum(n for reason, n in by_reason.items() if reason in DECISION_REASONS),
            budget=by_reason.get("budget", 0),
        )

    @router.get("/eval/sets", response_model=EvalSetList, responses=ERRORS)
    async def eval_sets(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        rows = db.fetchall(
            "SELECT s.*, (SELECT COUNT(*) FROM eval_items i WHERE i.set_id = s.id) AS items"
            " FROM eval_sets s ORDER BY s.created_at DESC"
        )
        return EvalSetList(items=[from_row(EvalSetView, r, items=int(r["items"])) for r in rows])

    @router.get("/eval/runs", response_model=EvalRunList, responses=ERRORS)
    async def eval_runs(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        set_id = request.query_params.get("set_id")
        if set_id is None:
            rows = db.fetchall("SELECT * FROM eval_runs ORDER BY started_at DESC, id DESC")
        else:
            rows = db.fetchall(
                "SELECT * FROM eval_runs WHERE set_id = ? ORDER BY started_at DESC, id DESC",
                (set_id,),
            )
        return EvalRunList(items=[deps.eval_run_view(r) for r in rows])

    @router.get("/eval/runs/{run_id}", response_model=EvalRunDetail, responses=ERRORS)
    async def eval_run(run_id: str, request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        row = writer.get_eval_run(run_id)
        if row is None:
            return error(404, "eval_run", f"no eval run {run_id}")
        results = [
            EvalResultView(
                item_id=str(r["item_id"]),
                issue_number=int(r["issue_number"]),
                eval_class=str(r["class"]),
                passed=bool(r["passed"]),
                cost_usd=r["cost_usd"],
                duration_s=r["duration_s"],
                failure_reason=r["failure_reason"],
            )
            for r in writer.eval_results(run_id)
        ]
        return EvalRunDetail(run=deps.eval_run_view(row), results=results)

    @router.get("/config", response_model=ConfigView, responses=ERRORS)
    async def config(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        installation_row = deps.installation()
        if installation_row is None:
            return error(503, "installation", "the installation row is missing")
        row = db.fetchone(
            "SELECT * FROM config_versions WHERE config_sha = ?", (installation_row["config_sha"],)
        )
        if row is None:
            return error(404, "config", "the installation's configuration version is not loaded")
        return _config_view(row)

    @router.get("/config/history", response_model=ConfigHistory, responses=ERRORS)
    async def config_history(request: Request) -> object:
        _caller, refused = deps.guard(request, "read")
        if refused is not None:
            return refused
        installation_row = deps.installation()
        current = None if installation_row is None else str(installation_row["config_sha"])
        rows = db.fetchall(
            "SELECT config_sha, main_commit, loaded_at FROM config_versions ORDER BY loaded_at DESC"
        )
        items: list[ConfigHistoryItem] = []
        for r in rows:
            run = db.fetchone(
                "SELECT * FROM eval_runs WHERE config_sha = ? AND status = 'complete'"
                " ORDER BY started_at DESC, id DESC LIMIT 1",
                (r["config_sha"],),
            )
            items.append(
                ConfigHistoryItem(
                    config_sha=str(r["config_sha"]),
                    main_commit=str(r["main_commit"]),
                    loaded_at=str(r["loaded_at"]),
                    current=r["config_sha"] == current,
                    eval=None if run is None else deps.eval_run_view(run),
                )
            )
        return ConfigHistory(items=items)

    @router.get("/changes", responses=STREAM_RESPONSES)
    async def changes(request: Request) -> object:
        caller = identify(request, deps.settings)
        if not caller.known:
            return error(403, "caller", "/changes takes the console's service token")
        if not deps.limiter.open_stream(f"changes:{caller.key}"):
            return limited("read", caller)
        generator = changes_stream(
            writer, on_close=lambda: deps.limiter.close_stream(f"changes:{caller.key}")
        )
        return StreamingResponse(generator, media_type="text/event-stream")

    return router


def _config_view(row: sqlite3.Row) -> ConfigView:
    protected = _json(row["protected_paths_json"])
    vendors = _json(row["vendors_json"])
    return ConfigView(
        config_sha=str(row["config_sha"]),
        main_commit=str(row["main_commit"]),
        roster=_json(row["roster_json"]),
        risk_yaml=str(row["risk_yaml"]),
        review_policy_yaml=str(row["review_policy_yaml"]),
        protected_paths=[str(p) for p in protected] if isinstance(protected, list) else [],
        quality_commands=_json(row["quality_commands_json"]),
        vendors=dict(vendors) if isinstance(vendors, dict) else {},
        loaded_at=str(row["loaded_at"]),
    )


_CHAIN_CACHE: dict[
    str, tuple[int, int, ChainReport]
] = {}  # task -> (start, verified through, report)


def chain_report(db: TraceDB, task_id: str, start: int, end: int) -> ChainReport:
    """The span's verification; a span this process verified before grows from its last row.

    Rows are append-only and hash-chained, so a verified prefix stays verified and only the
    rows after the last verified one are recomputed (TRD §3.4: the detail read under 2 s with
    10,000 rows). A broken chain is never cached; ``factory verify`` and the export recompute
    every row.
    """
    cached = _CHAIN_CACHE.get(task_id)
    if cached is None or cached[0] != start or not cached[2].ok or cached[1] > end:
        report = verify_chain(db, start, end)
    else:
        _, through, previous = cached
        if through >= end:
            return previous
        tail = verify_chain(db, through, end)
        report = ChainReport(
            ok=previous.ok and tail.ok,
            records=previous.records + max(tail.records - 1, 0),
            head_hash=tail.head_hash or previous.head_hash,
            first_bad_seq=previous.first_bad_seq or tail.first_bad_seq,
            reason=tail.reason or previous.reason,
        )
    last = db.scalar("SELECT MAX(seq) FROM events WHERE seq >= ? AND seq <= ?", (start, end))
    if report.ok and last is not None:
        _CHAIN_CACHE[task_id] = (start, int(last), report)
    return report


def open_incident_seq(db: TraceDB) -> int | None:
    """The newest incident no admissions resume has cleared (PRD leading indicators)."""
    cleared = db.scalar(
        "SELECT MAX(json_extract(payload_json, '$.clear_incident')) FROM events"
        " WHERE kind = 'control' AND json_extract(payload_json, '$.action') = 'admissions'"
    )
    seq = db.scalar(
        "SELECT MAX(seq) FROM events WHERE kind = 'incident' AND seq > ?", (int(cleared or 0),)
    )
    return None if seq is None else int(seq)


def last_kill_switch(db: TraceDB) -> KillSwitchNote | None:
    """Who last turned the factory off or on, from the control event (the dialog's note)."""
    row = db.fetchone(
        "SELECT actor, created_at, payload_json FROM events WHERE kind = 'control'"
        " AND json_extract(payload_json, '$.action') = 'kill_switch' ORDER BY seq DESC LIMIT 1"
    )
    if row is None:
        return None
    payload = json.loads(row["payload_json"] or "{}")
    return KillSwitchNote(
        actor=str(row["actor"]),
        at=str(row["created_at"]),
        enabled=bool(payload.get("enabled", 0)),
        reason=payload.get("reason"),
    )


def last_dry_run(db: TraceDB) -> DryRunNote | None:
    """The installer's last budget derivation, from the log event the dry run writes."""
    row = db.fetchone(
        "SELECT created_at, payload_json FROM events WHERE kind = 'log'"
        " AND json_extract(payload_json, '$.dry_run') IS NOT NULL ORDER BY seq DESC LIMIT 1"
    )
    if row is None:
        return None
    payload = json.loads(row["payload_json"] or "{}")
    return DryRunNote(
        at=str(row["created_at"]),
        outcome=str(payload.get("dry_run")),
        issues=list(payload.get("issues") or []),
        completed=int(payload.get("completed") or 0),
        required=int(payload.get("required") or 0),
        highest_cost_usd=payload.get("highest_cost_usd"),
        factor=float(payload.get("factor") or 0.0),
        per_task_budget_usd=payload.get("per_task_budget_usd"),
        floor_usd=payload.get("floor_usd"),
        reason=str(payload.get("reason") or ""),
    )


def local_today(deps: ReadDeps) -> str:
    return local_day(deps.clock(), deps.timezone())


__all__ = [
    "INDICATOR_THRESHOLDS",
    "KPI_TARGETS",
    "PAGE_LIMIT",
    "ReadDeps",
    "decode_cursor",
    "encode_cursor",
    "from_row",
    "git_show",
    "local_today",
    "parse_limit",
    "parse_questions",
    "read_router",
]
