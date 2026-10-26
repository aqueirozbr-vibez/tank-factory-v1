"""API response schemas (TRD §6.1); the OpenAPI document is generated from these models.

Every list is ``{items, next}`` with an opaque cursor; every error is
``{code, message, detail?, traceId}``. The shapes carry trace rows as they
are, with JSON columns parsed, so the console renders what the trace says
and computes nothing of its own (architecture.md · Invariants).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ErrorBody(ApiModel):
    code: str
    message: str
    detail: object | None = None
    trace_id: str = Field(alias="traceId")


# --- installation and configuration ---------------------------------------------------------------


class KillSwitchNote(ApiModel):
    actor: str
    at: str
    enabled: bool
    reason: str | None


class DryRunNote(ApiModel):
    """The installer's last budget derivation (the console's Install dry run panel)."""

    at: str
    outcome: str  # derived | refused
    issues: list[object]
    completed: int
    required: int
    highest_cost_usd: float | None
    factor: float
    per_task_budget_usd: float | None
    floor_usd: float | None = None
    reason: str


class InstallationView(ApiModel):
    org: str
    repo: str
    factory_version: str
    config_sha: str
    concurrency_n: int
    budget_mode: Literal["install", "median"]
    per_task_budget_usd: float
    daily_cap_usd: float
    retention_days: int
    factory_enabled: bool
    admissions_paused: bool
    timezone: str
    export_pubkey: str
    created_at: str
    updated_at: str
    spent_today_usd: float  # today's phase spend against daily_cap_usd (Overview meter)
    open_incident_seq: int | None = None  # the incident an admissions resume may clear
    last_kill_switch: KillSwitchNote | None = None  # who last turned the factory off or on
    last_dry_run: DryRunNote | None = None  # the install derivation (spec 67)


class ConfigView(ApiModel):
    config_sha: str
    main_commit: str
    roster: object
    risk_yaml: str
    review_policy_yaml: str
    protected_paths: list[str]
    quality_commands: object
    vendors: dict[str, str]
    loaded_at: str


class ConfigHistoryItem(ApiModel):
    config_sha: str
    main_commit: str
    loaded_at: str
    current: bool
    eval: "EvalRunView | None" = None  # the newest eval run recorded against this sha


class ConfigHistory(ApiModel):
    items: list[ConfigHistoryItem]
    next: str | None = None


# --- tasks ----------------------------------------------------------------------------------------


class TaskSummary(ApiModel):
    task_id: str
    issue_number: int
    title: str | None
    status: str
    blocked_reason: str | None
    risk: str | None
    risk_score: float | None
    governed_path: str | None
    branch: str
    workspace_kind: str
    spec_path: str | None
    spec_sha: str | None
    pr_number: int | None
    pr_head_sha: str | None
    budget_usd: float
    spent_usd: float
    attempt: int
    admitted_by: str
    created_at: str
    updated_at: str


class TaskPage(ApiModel):
    items: list[TaskSummary]
    next: str | None = None


class RunView(ApiModel):
    run_id: str
    attempt: int
    workflow: str
    status: str
    factory_version: str
    config_sha: str
    started_at: str
    ended_at: str | None
    tokens_in: int
    tokens_out: int
    total_cost_usd: float


class PhaseView(ApiModel):
    phase_id: str
    run_id: str
    seq: int
    name: str
    kind: str
    owner: str
    status: str
    attempt: int
    retries: int
    harness: str | None
    harness_version: str | None
    model_id: str | None
    session_id: str | None
    timeout_s: int
    phase_cap_usd: float | None
    tokens_in: int | None
    tokens_out: int | None
    tokens_cache_read: int | None
    tokens_cache_write: int | None
    cost_usd: float | None
    started_at: str
    ended_at: str | None
    error: str | None


class ApprovalView(ApiModel):
    id: str
    gate: str
    actor_login: str
    bound_sha: str
    governed_path: str | None
    codeowner: bool | None
    reason: str | None
    github_label: str
    github_event_id: str
    phase_id: str | None
    created_at: str


class BudgetView(ApiModel):
    budget_usd: float
    spent_usd: float
    remaining_usd: float
    raised: bool
    last_stop: object | None = None  # the newest ``budget_stop`` payload, if any


class LinksView(ApiModel):
    issue: str
    branch: str
    spec: str | None
    pr: str | None


class TaskDetail(ApiModel):
    task: TaskSummary
    runs: list[RunView]
    phases: list[PhaseView]
    approvals: list[ApprovalView]
    budget: BudgetView
    links: LinksView
    chain: "ChainFooter"


class ChainFooter(ApiModel):
    verified: bool
    records: int
    head_hash: str | None
    first_bad_seq: int | None = None
    head_seq: int | None = None  # the client's starting cursor for the newest page


class EventView(ApiModel):
    seq: int
    kind: str
    actor: str
    task_id: str | None
    run_id: str | None
    phase_id: str | None
    parent_seq: int | None
    payload: object | None
    tokens: int | None
    started_at: str | None
    ended_at: str | None
    created_at: str
    hash: str


class EventPage(ApiModel):
    items: list[EventView]
    next: str | None = None


class EnvelopeView(ApiModel):
    envelope_id: str
    run_id: str
    phase_id: str
    agent: str
    output_type: str
    payload: object | None
    valid: bool
    violations: object | None
    attempt: int
    created_at: str


class EnvelopeList(ApiModel):
    items: list[EnvelopeView]
    next: str | None = None


class GateView(ApiModel):
    id: str
    run_id: str
    phase_id: str
    attempt: int
    gate: str
    passed: bool
    checks: object
    violations: object | None
    command: str | None
    exit_code: int | None
    output_tail: str | None
    created_at: str


class GateList(ApiModel):
    items: list[GateView]
    next: str | None = None


class ReviewView(ApiModel):
    round: int
    rounds: int
    phase_id: str | None
    verdict: str
    reviewer_verdict: str | None
    conformance: float | None
    blocking_score: float | None
    strongest: object | None
    findings: list[object]
    dismissed: list[object]
    human_reasons: list[str]
    missing_ac: list[str]
    policy_version: int | None
    accept_conformance: float | None = None  # the policy's accept threshold (PRD Cap. 4)
    overridden_by: str | None
    override_reason: str | None
    envelope: object | None  # the reviewer's ReviewOutput as recorded


# --- approvals and audit --------------------------------------------------------------------------


class PlanQueueItem(ApiModel):
    task_id: str
    issue_number: int
    title: str | None
    risk: str | None
    risk_score: float | None
    governed_path: str | None
    spec_path: str | None
    spec_sha: str | None
    spec_text: str | None
    risk_breakdown: object | None
    ac_holdout: list[object]
    waiting_since: str
    phase_id: str


class PlanQueue(ApiModel):
    items: list[PlanQueueItem]
    next: str | None = None


class QuestionItem(ApiModel):
    task_id: str
    issue_number: int
    title: str | None
    questions: list[str]
    language: str
    blocked_at: str


class QuestionQueue(ApiModel):
    items: list[QuestionItem]
    next: str | None = None


class AuditQueueItem(ApiModel):
    id: str
    task_id: str
    pr: int
    month: str
    stratum: str
    risk: str | None
    sampled: bool


class AuditQueue(ApiModel):
    items: list[AuditQueueItem]
    next: str | None = None


class NeedsHumanCount(ApiModel):
    plans: int
    questions: int
    decisions: int
    budget: int


# --- metrics --------------------------------------------------------------------------------------


class MetricView(ApiModel):
    metric: str
    value: float | None
    numerator: float | None
    denominator: float | None
    target: str | None = None
    thresholds: dict[str, float] | None = None


class KpiResponse(ApiModel):
    day: str | None
    window: str
    items: list[MetricView]


class IndicatorResponse(ApiModel):
    day: str | None
    items: list[MetricView]


# --- eval -----------------------------------------------------------------------------------------


class EvalSetView(ApiModel):
    id: str
    name: str
    repo: str
    reference_pass_rate: float | None
    created_at: str
    items: int


class EvalSetList(ApiModel):
    items: list[EvalSetView]
    next: str | None = None


class EvalRunView(ApiModel):
    id: str
    set_id: str
    factory_version: str
    config_sha: str
    trigger: str
    trigger_ref: str | None
    baseline_run_id: str | None
    started_at: str
    ended_at: str | None
    status: str
    pass_rate: float | None
    per_class: dict[str, float] | None
    delta_points: float | None
    blocked: bool | None


class EvalRunList(ApiModel):
    items: list[EvalRunView]
    next: str | None = None


class EvalResultView(ApiModel):
    item_id: str
    issue_number: int
    eval_class: str
    passed: bool
    cost_usd: float | None
    duration_s: int | None
    failure_reason: str | None


class EvalRunDetail(ApiModel):
    run: EvalRunView
    results: list[EvalResultView]


# --- write requests (appended to orchestrator/models/api.py by unit 55) ---------------------------


class BudgetRaiseRequest(ApiModel):
    budget_usd: float = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)


class ResumeRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=500)


class RiskOverrideRequest(ApiModel):
    risk: Literal["risk:high", "risk:governed"]
    reason: str = Field(min_length=1, max_length=500)


class AuditDecisionRequest(ApiModel):
    decision: Literal["agree", "disagree"]
    reason: str = Field(min_length=1, max_length=500)
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class ConcurrencyRequest(ApiModel):
    n: int
    reason: str = Field(min_length=1, max_length=500)


class KillSwitchRequest(ApiModel):
    enabled: Literal[0, 1]
    reason: str = Field(min_length=1, max_length=500)


class AdmissionsRequest(ApiModel):
    paused: Literal[0, 1]
    reason: str = Field(min_length=1, max_length=500)
    clear_incident: int | None = None  # the incident event seq an operator clears while resuming


class EvalRunRequest(ApiModel):
    set_id: str = Field(min_length=1)
    trigger: Literal["manual"] = "manual"


ConfigHistoryItem.model_rebuild()
TaskDetail.model_rebuild()

__all__ = [
    "AdmissionsRequest",
    "ApiModel",
    "ApprovalView",
    "AuditDecisionRequest",
    "AuditQueue",
    "AuditQueueItem",
    "BudgetRaiseRequest",
    "BudgetView",
    "ChainFooter",
    "ConcurrencyRequest",
    "ConfigHistory",
    "ConfigHistoryItem",
    "ConfigView",
    "EnvelopeList",
    "EnvelopeView",
    "ErrorBody",
    "EvalResultView",
    "EvalRunDetail",
    "EvalRunList",
    "EvalRunRequest",
    "EvalRunView",
    "EvalSetList",
    "EvalSetView",
    "EventPage",
    "EventView",
    "GateList",
    "GateView",
    "IndicatorResponse",
    "InstallationView",
    "KillSwitchRequest",
    "KpiResponse",
    "LinksView",
    "MetricView",
    "NeedsHumanCount",
    "PhaseView",
    "PlanQueue",
    "PlanQueueItem",
    "QuestionItem",
    "QuestionQueue",
    "ResumeRequest",
    "ReviewView",
    "RiskOverrideRequest",
    "RunView",
    "TaskDetail",
    "TaskPage",
    "TaskSummary",
]
