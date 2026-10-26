"""Enumerations whose values are the schema's CHECK strings (TRD §4.2).

These are the PRD's names. Nothing re-spells them.
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    PR_OPEN = "pr_open"
    MERGED = "merged"
    CANCELLED = "cancelled"


class BlockedReason(StrEnum):
    OPEN_QUESTIONS = "open_questions"
    MISSING_HOLDOUT = "missing_holdout"
    SPEC_GATE = "spec_gate"
    PROTECTED_PATH = "protected_path"
    REPAIR_CAP = "repair_cap"
    REVISION_CAP = "revision_cap"
    HUMAN_DECISION_NEEDED = "human_decision_needed"
    BUDGET = "budget"
    TIMEOUT = "timeout"
    DEPENDENCY_CHANGE = "dependency_change"
    FULL_SUITE = "full_suite"


class RiskClass(StrEnum):
    LOW = "risk:low"
    HIGH = "risk:high"
    GOVERNED = "risk:governed"


class WorkspaceKind(StrEnum):
    WORKTREE = "worktree"
    CONTAINER = "container"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAIL = "fail"


class PhaseName(StrEnum):
    ADMISSION = "admission"
    PLAN = "plan"
    SPEC_GATE = "spec_gate"
    RISK = "risk"
    PLAN_APPROVAL = "plan_approval"
    BUILD = "build"
    CODE_GATES = "code_gates"
    COMMIT = "commit"
    REVIEW = "review"
    REVISION = "revision"
    FULL_SUITE = "full_suite"
    DELIVER = "deliver"
    CI_WAIT = "ci_wait"


class PhaseKind(StrEnum):
    ENGINEER = "engineer"
    AGENT = "agent"
    CODE = "code"


class PhaseStatus(StrEnum):
    RUNNING = "running"
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class EventKind(StrEnum):
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TOOL_CALL = "tool_call"
    HANDOFF = "handoff"
    GATE_PASS = "gate_pass"
    GATE_FAIL = "gate_fail"
    LOG = "log"
    ERROR = "error"
    WEBHOOK = "webhook"
    POLL = "poll"
    LABEL_WRITE = "label_write"
    COMMENT_WRITE = "comment_write"
    CONTROL = "control"
    APPROVAL = "approval"
    AUDIT = "audit"
    EGRESS_DENIED = "egress_denied"
    BUDGET_STOP = "budget_stop"
    PURGE = "purge"
    EXPORT = "export"
    INCIDENT = "incident"


class GateName(StrEnum):
    ENVELOPE_SCHEMA = "envelope_schema"
    ARTIFACTS_EXIST = "artifacts_exist"
    FILES_NON_EMPTY = "files_non_empty"
    DIFF_MATCHES_CLAIMS = "diff_matches_claims"
    PERMISSIONS_ENFORCE = "permissions_enforce"
    SPEC_SECTIONS = "spec_sections"
    SPEC_EARS = "spec_ears"
    SPEC_AC_HOLDOUT_MAP = "spec_ac_holdout_map"
    SPEC_ADR_CITED = "spec_adr_cited"
    FORMAT = "format"
    LINT = "lint"
    TYPECHECK = "typecheck"
    FOCUSED_TESTS = "focused_tests"
    VERDICT_CONSISTENT = "verdict_consistent"
    FULL_SUITE = "full_suite"


class AgentRole(StrEnum):
    PLANNER = "planner"
    BUILDER = "builder"
    REVIEWER = "reviewer"


class OutputType(StrEnum):
    PLAN = "PlanOutput"
    BUILD = "BuildOutput"
    REVIEW = "ReviewOutput"
    VERIFY = "VerifyOutput"


class ProcessKind(StrEnum):
    AGENT = "agent"
    COMMAND = "command"
    CONTAINER = "container"


class ApprovalGate(StrEnum):
    PLAN = "plan"
    GOVERNED = "governed"
    RISK_OVERRIDE = "risk_override"


class BudgetMode(StrEnum):
    INSTALL = "install"
    MEDIAN = "median"
