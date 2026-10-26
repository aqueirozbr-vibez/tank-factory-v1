"""The write endpoints of ``/api/v1`` (TRD §6.1, §6.2; code-standards · API Routes).

Every handler runs the same order: parse the body, identify the caller,
resolve the actor's GitHub permission, evaluate the authz table, honour
``Idempotency-Key``, apply the rule's rate limit, act through a service,
write exactly one ``control`` event under the actor's login, remember the
response for the key. A denial is a 403 and a ``control`` event too.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from orchestrator import constants
from orchestrator.api.limits import Caller, RateLimiter, error, identify, limited
from orchestrator.authz import Decision, decide
from orchestrator.budget.controls import raise_task_budget
from orchestrator.intake.resume import Resume
from orchestrator.intake.scheduler import Scheduler
from orchestrator.models.api import (
    AdmissionsRequest,
    ApiModel,
    AuditDecisionRequest,
    BudgetRaiseRequest,
    ConcurrencyRequest,
    EvalRunRequest,
    KillSwitchRequest,
    ResumeRequest,
    RiskOverrideRequest,
)
from orchestrator.models.enums import ApprovalGate, EventKind, TaskStatus
from orchestrator.models.settings import Settings
from orchestrator.trace.kpi import local_month
from orchestrator.trace.writer import TraceWriter
from orchestrator.watchdogs import KillSwitch

IDEMPOTENCY_HEADER = "Idempotency-Key"
PermissionFor = Callable[[str], Awaitable[str | None]]
CodeownerOf = Callable[[str, str], Awaitable[bool]]
StartEval = Callable[[str, str], Awaitable[str]]
Action = Literal[
    "budget_raise",
    "resume",
    "risk_override",
    "audit_decide",
    "concurrency",
    "kill_switch",
    "admissions",
    "eval_run",
]
RULE_FOR_ACTION: dict[str, str] = {
    "budget_raise": "write",
    "resume": "write",
    "risk_override": "write",
    "audit_decide": "audit",
    "concurrency": "write",
    "kill_switch": "kill_switch",
    "admissions": "write",
    "eval_run": "eval_run",
}


@dataclass
class ControlDeps:
    writer: TraceWriter
    settings: Settings
    limiter: RateLimiter
    permission_for: PermissionFor
    codeowner_of: CodeownerOf | None = None
    scheduler: Scheduler | None = None
    kill_switch: KillSwitch | None = None
    resume: Resume | None = None
    start_eval: StartEval | None = None
    clock: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))


@dataclass(frozen=True)
class Gate:
    """What every write passes before it acts, or the response that stops it."""

    caller: Caller
    actor: str
    key: str
    body_sha: str
    refused: JSONResponse | None = None


def body_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


async def gate(
    deps: ControlDeps,
    request: Request,
    *,
    action: Action,
    route: str,
    task_id: str | None = None,
    codeowner_path: str | None = None,
) -> tuple[Gate, bytes]:
    """Caller, actor, permission, authz, idempotency replay and rate limit, in that order."""
    raw = await request.body()
    caller = identify(request, deps.settings)
    blank = Gate(caller, "", "", body_digest(raw))
    if not caller.known:
        return Gate(
            caller,
            "",
            "",
            blank.body_sha,
            error(403, "caller", "writes take the console's service token with X-Factory-Actor"),
        ), raw
    if not caller.actor:
        return Gate(
            caller, "", "", blank.body_sha, error(400, "actor", "X-Factory-Actor names who acts")
        ), raw
    key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
    if not key:
        return Gate(
            caller,
            caller.actor,
            "",
            blank.body_sha,
            error(400, "idempotency_key", "every write carries an Idempotency-Key"),
        ), raw
    permission = await deps.permission_for(caller.actor)
    codeowner = False
    if codeowner_path is not None and deps.codeowner_of is not None:
        codeowner = await deps.codeowner_of(caller.actor, codeowner_path)
    decision: Decision = decide(permission, action, codeowner=codeowner)
    if not decision.allowed:
        # Linked to the task only when it exists; the attempt names it either way.
        linked = task_id if task_id and deps.writer.get_task(task_id) is not None else None
        denied: dict[str, object] = {
            "action": "denied",
            "attempted": action,
            "permission": permission,
            "reason": decision.reason,
        }
        if task_id:
            denied["task_id"] = task_id
        deps.writer.write_event(EventKind.CONTROL, caller.actor, task_id=linked, payload=denied)
        return Gate(
            caller,
            caller.actor,
            key,
            blank.body_sha,
            error(403, f"{action}_denied", decision.reason, {"permission": permission}),
        ), raw
    remembered = deps.writer.recall_response(key=key, route=route, actor=caller.actor)
    if remembered is not None:
        if str(remembered["request_sha256"]) != blank.body_sha:
            return Gate(
                caller,
                caller.actor,
                key,
                blank.body_sha,
                error(422, "idempotency_body", "this Idempotency-Key was used with another body"),
            ), raw
        return Gate(
            caller,
            caller.actor,
            key,
            blank.body_sha,
            JSONResponse(
                json.loads(str(remembered["response_json"])), status_code=int(remembered["status"])
            ),
        ), raw
    rule = RULE_FOR_ACTION[action]
    if not deps.limiter.allow(caller.key, rule):
        return Gate(caller, caller.actor, key, blank.body_sha, limited(rule, caller)), raw
    return Gate(caller, caller.actor, key, blank.body_sha), raw


def remember(
    deps: ControlDeps, passed: Gate, *, route: str, response: JSONResponse
) -> JSONResponse:
    deps.writer.remember_response(
        key=passed.key,
        route=route,
        actor=passed.actor,
        request_sha256=passed.body_sha,
        status=response.status_code,
        body_json=bytes(response.body).decode("utf-8"),
    )
    return response


def parse[M: ApiModel](model: type[M], raw: bytes) -> M | JSONResponse:
    try:
        return model.model_validate_json(raw or b"{}")
    except ValidationError as exc:
        return error(
            400, "body", f"body does not match {model.__name__}", exc.errors(include_url=False)
        )


def control_router(deps: ControlDeps) -> APIRouter:
    router = APIRouter()
    writer = deps.writer

    @router.post("/tasks/{task_id:path}/budget")
    async def raise_budget(task_id: str, request: Request) -> JSONResponse:
        route = "POST /tasks/{task_id}/budget"
        passed, raw = await gate(deps, request, action="budget_raise", route=route, task_id=task_id)
        if passed.refused is not None:
            return passed.refused
        body = parse(BudgetRaiseRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        outcome = raise_task_budget(
            writer,
            task_id=task_id,
            new_budget_usd=body.budget_usd,
            actor=passed.actor,
            reason=body.reason,
        )
        if not outcome.ok:
            response = error(outcome.http_status, f"budget_{outcome.code}", outcome.message)
        else:
            response = JSONResponse(
                {
                    "old": outcome.old_budget_usd,
                    "new": outcome.new_budget_usd,
                    "event_seq": outcome.event_seq,
                }
            )
        return remember(deps, passed, route=route, response=response)

    @router.post("/tasks/{task_id:path}/resume")
    async def resume_task(task_id: str, request: Request) -> JSONResponse:
        route = "POST /tasks/{task_id}/resume"
        passed, raw = await gate(deps, request, action="resume", route=route, task_id=task_id)
        if passed.refused is not None:
            return passed.refused
        body = parse(ResumeRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        if deps.resume is None:
            return error(503, "resume_unavailable", "this process does not run the scheduler")
        result = await deps.resume.apply(task_id, actor=passed.actor, reason=body.reason)
        if result.kind == "ignored":
            response = error(404, "resume_task", f"{task_id} is not a blocked task")
        elif result.kind in ("not_resumable", "budget_requires_raise"):
            response = error(
                409, f"resume_{result.kind}", result.rule or "this block does not resume"
            )
        else:
            response = JSONResponse(
                {"status": "queued", "blocked_reason": str(result.reason), "rule": result.rule}
            )
        return remember(deps, passed, route=route, response=response)

    @router.post("/tasks/{task_id:path}/risk-override")
    async def risk_override(task_id: str, request: Request) -> JSONResponse:
        route = "POST /tasks/{task_id}/risk-override"
        task = writer.get_task(task_id)
        passed, raw = await gate(
            deps, request, action="risk_override", route=route, task_id=task_id
        )
        if passed.refused is not None:
            return passed.refused
        body = parse(RiskOverrideRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        if task is None:
            response = error(404, "risk_override_task", f"no task {task_id}")
        elif task["status"] in (
            str(TaskStatus.PR_OPEN),
            str(TaskStatus.MERGED),
            str(TaskStatus.CANCELLED),
        ):
            response = error(409, "risk_override_late", "a risk override is accepted until pr_open")
        else:
            writer.set_task_risk(
                task_id,
                risk=body.risk,
                score=float(task["risk_score"] or 0.0),
                overridden_by=passed.actor,
                governed_path=task["governed_path"],
            )
            approval_id = writer.record_approval(
                task_id=task_id,
                gate=ApprovalGate.RISK_OVERRIDE,
                actor_login=passed.actor,
                bound_sha=str(task["spec_sha"] or ""),
                github_event_id=f"console:{passed.key}",
                reason=body.reason,
            )
            seq = writer.write_event(
                EventKind.CONTROL,
                passed.actor,
                task_id=task_id,
                payload={
                    "action": "risk_override",
                    "old": task["risk"],
                    "new": body.risk,
                    "reason": body.reason,
                    "approval_id": approval_id,
                },
            )
            response = JSONResponse(
                {"approval_id": approval_id, "event_seq": seq, "risk": body.risk}
            )
        return remember(deps, passed, route=route, response=response)

    @router.post("/audit/{task_id:path}")
    async def audit_decide(task_id: str, request: Request) -> JSONResponse:
        route = "POST /audit/{task_id}"
        passed, raw = await gate(deps, request, action="audit_decide", route=route, task_id=task_id)
        if passed.refused is not None:
            return passed.refused
        body = parse(AuditDecisionRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        installation = writer.get_installation()
        timezone = str(installation["timezone"]) if installation is not None else "UTC"
        month = body.month or local_month(deps.clock(), timezone)
        row = writer.db.fetchone(
            "SELECT decision FROM audit_decisions WHERE task_id = ? AND month = ?", (task_id, month)
        )
        if row is None:
            response = error(404, "audit_not_drawn", f"{task_id} is not in the {month} audit queue")
        elif row["decision"] is not None:
            response = error(409, "audit_decided", f"{task_id} is already decided for {month}")
        else:
            seq = writer.decide_audit(
                task_id=task_id,
                month=month,
                decision=body.decision,
                reason=body.reason,
                by=passed.actor,
            )
            response = JSONResponse({"event_seq": seq, "month": month, "decision": body.decision})
        return remember(deps, passed, route=route, response=response)

    @router.post("/controls/concurrency")
    async def concurrency(request: Request) -> JSONResponse:
        route = "POST /controls/concurrency"
        passed, raw = await gate(deps, request, action="concurrency", route=route)
        if passed.refused is not None:
            return passed.refused
        body = parse(ConcurrencyRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        installation = writer.get_installation()
        old = int(installation["concurrency_n"]) if installation is not None else 0
        if not constants.CONCURRENCY_MIN <= body.n <= constants.CONCURRENCY_MAX:
            response = error(
                422,
                "concurrency_bounds",
                f"N is {constants.CONCURRENCY_MIN} to {constants.CONCURRENCY_MAX}",
            )
        else:
            writer.set_concurrency(body.n)
            seq = writer.write_event(
                EventKind.CONTROL,
                passed.actor,
                payload={"action": "concurrency", "old": old, "new": body.n, "reason": body.reason},
            )
            if deps.scheduler is not None:
                await deps.scheduler.dispatch()
            response = JSONResponse({"old": old, "new": body.n, "event_seq": seq})
        return remember(deps, passed, route=route, response=response)

    @router.post("/controls/kill-switch")
    async def kill_switch(request: Request) -> JSONResponse:
        route = "POST /controls/kill-switch"
        passed, raw = await gate(deps, request, action="kill_switch", route=route)
        if passed.refused is not None:
            return passed.refused
        body = parse(KillSwitchRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        switch = deps.kill_switch
        if switch is None:
            return error(
                503, "kill_switch_unavailable", "this process does not operate the kill-switch"
            )
        enabled = bool(body.enabled)
        if enabled == switch.enabled():
            response = error(409, "kill_switch_state", f"factory_enabled is already {int(enabled)}")
        else:
            report = (
                await switch.enable(actor=passed.actor, reason=body.reason)
                if enabled
                else await switch.disable(actor=passed.actor, reason=body.reason)
            )
            response = JSONResponse(
                {
                    "factory_enabled": report.enabled,
                    "live_processes": report.live_after,
                    "event_seq": report.event_seq,
                    "cancelled": list(report.cancelled),
                    "containers": list(report.containers),
                    "elapsed_s": report.elapsed_s,
                }
            )
        return remember(deps, passed, route=route, response=response)

    @router.post("/controls/admissions")
    async def admissions(request: Request) -> JSONResponse:
        route = "POST /controls/admissions"
        passed, raw = await gate(deps, request, action="admissions", route=route)
        if passed.refused is not None:
            return passed.refused
        body = parse(AdmissionsRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        paused = bool(body.paused)
        if paused == writer.admissions_paused():
            response = error(409, "admissions_state", f"admissions_paused is already {int(paused)}")
        else:
            writer.set_admissions_paused(paused)
            payload: dict[str, object] = {
                "action": "admissions",
                "paused": paused,
                "reason": body.reason,
            }
            if body.clear_incident is not None:
                payload["clear_incident"] = body.clear_incident
            seq = writer.write_event(EventKind.CONTROL, passed.actor, payload=payload)
            if not paused and deps.scheduler is not None:
                await deps.scheduler.dispatch()
            response = JSONResponse({"paused": paused, "event_seq": seq})
        return remember(deps, passed, route=route, response=response)

    @router.post("/eval/runs")
    async def eval_runs(request: Request) -> JSONResponse:
        route = "POST /eval/runs"
        passed, raw = await gate(deps, request, action="eval_run", route=route)
        if passed.refused is not None:
            return passed.refused
        body = parse(EvalRunRequest, raw)
        if isinstance(body, JSONResponse):
            return body
        if deps.start_eval is None:
            return error(503, "eval_unavailable", "this process does not run evals")
        items = len(writer.eval_items(body.set_id))
        running = writer.db.fetchone("SELECT id FROM eval_runs WHERE status = 'running' LIMIT 1")
        if writer.eval_set(body.set_id) is None:
            response = error(404, "eval_set", f"no eval set {body.set_id}")
        elif items < constants.EVAL_MIN_ITEMS:
            response = error(
                422, "eval_set_size", f"an eval set needs at least {constants.EVAL_MIN_ITEMS} items"
            )
        elif running is not None:
            response = error(409, "eval_running", f"eval run {running['id']} is in progress")
        else:
            run_id = await deps.start_eval(body.set_id, passed.actor)
            seq = writer.write_event(
                EventKind.CONTROL,
                passed.actor,
                payload={"action": "eval_run", "set_id": body.set_id, "run_id": run_id},
            )
            response = JSONResponse({"run_id": run_id, "event_seq": seq}, status_code=202)
        return remember(deps, passed, route=route, response=response)

    return router


__all__ = ["IDEMPOTENCY_HEADER", "RULE_FOR_ACTION", "ControlDeps", "Gate", "control_router", "gate"]
