"""Every number the PRD fixes, defined once (code-standards.md · General).

Each constant names the PRD section that fixes it. No module re-declares
these values; a cap that lives in a prompt instead of here is a defect.
"""

from typing import Final

# --- Caps in the state machine (PRD §Capability 2, 3, 4, 5) -----------------
REPAIR_CAP: Final[int] = 3  # builder repairs per build attempt (Cap. 3)
REVISION_CAP: Final[int] = 2  # reviewer revision rounds (Cap. 4)
SPEC_CORRECTION_CAP: Final[int] = 2  # planner corrections after a spec-gate failure (Cap. 2)
ENVELOPE_REPROMPT_CAP: Final[int] = 1  # re-prompt on an envelope parse failure (Cap. 2, 4)
VERDICT_REPROMPT_CAP: Final[int] = 1  # verdict_consistent re-prompt (Cap. 4)
FULL_SUITE_REPAIR_CAP: Final[int] = 1  # repair after a full-suite failure (Cap. 5)
BUILDER_MAX_TURNS: Final[int] = 80  # --max-turns for the builder (Cap. 3)

# --- Timeouts and timing (PRD §Non-functional · Performance, Cap. 1, 5) ------
PLAN_TIMEOUT_S: Final[int] = 30 * 60
BUILD_TIMEOUT_S: Final[int] = 120 * 60  # build and repair phases
REVIEW_TIMEOUT_S: Final[int] = 45 * 60
STUCK_AFTER_S: Final[int] = 15 * 60  # no event for 15 min flags a stuck run (Cap. 5)
KILL_SWITCH_BOUND_S: Final[int] = 60  # SIGTERM lands within 60 s (Cap. 1, 5)
PROCESS_GRACE_S: Final[int] = 10  # SIGTERM to SIGKILL grace (TRD §5.4)
KILL_SWITCH_POLL_S: Final[int] = 5  # the running service re-reads the flag this often
POLL_INTERVAL_S: Final[int] = 60  # reconciliation poller (TRD §2.0)
PERMISSION_CACHE_S: Final[int] = 5 * 60  # GitHub collaborator permission cache (TRD §6.2)
PLAN_APPROVAL_TIMEOUT_S: Final[int] = 7 * 24 * 3600  # waiter row bound; the PRD fixes none
CI_WAIT_TIMEOUT_S: Final[int] = 7 * 24 * 3600  # ci_wait row bound; the PRD fixes none
SESSION_HOURS: Final[int] = 8  # console session and user token (TRD §3.4)
SPEND_WATCHDOG_INTERVAL_S: Final[int] = 10  # proxy spend read interval (TRD §6.3)

# --- Stored text caps (PRD §Capability 3, 5) --------------------------------
OUTPUT_TAIL_CHARS: Final[int] = 4_000  # last characters of command output
RESULT_SNIPPET_CHARS: Final[int] = 1_000  # tool_call result snippet (TRD §9)

# --- Concurrency and retention (PRD §Capability 1, §Non-functional · Privacy)
CONCURRENCY_DEFAULT: Final[int] = 3
CONCURRENCY_MIN: Final[int] = 1
CONCURRENCY_MAX: Final[int] = 10
RETENTION_DAYS_DEFAULT: Final[int] = 90
RETENTION_DAYS_MIN: Final[int] = 30
RETENTION_DAYS_MAX: Final[int] = 365
METADATA_RETENTION_DAYS: Final[int] = 365

# --- Instruments (PRD §Task risk classification, §Review verdict policy) ----
RISK_HIGH_THRESHOLD: Final[float] = 50.0  # score >= 50 needs plan approval
RISK_LOW_FLOOR: Final[float] = 1.0  # below 1 (tier U at 0) blocks for spec correction
CONFORMANCE_ACCEPT: Final[float] = 70.0  # conformance >= 70 is accepted

# --- Budgets and eval (PRD §Capability 5, §Non-functional · Cost control) ---
BUDGET_RAISE_MAX_FACTOR: Final[float] = 2.0  # one raise per task, at most 2x
DAILY_CAP_MULTIPLIER: Final[int] = 3  # daily cap = N x 3 x per-task budget
INSTALL_BUDGET_FACTOR: Final[float] = 2.0  # 2x the highest dry-run cost
INDUSTRY_PER_TASK_CAP_USD: Final[float] = (
    5.0  # the install floor: the per-task cap in common use (spec 67)
)
MEDIAN_BUDGET_FACTOR: Final[float] = 2.0  # 2x the trailing 30-day median
BUDGET_MEDIAN_WINDOW_DAYS: Final[int] = 30
BUDGET_INSTALL_PERIOD_DAYS: Final[int] = 7
DRY_RUN_ISSUES: Final[int] = 3  # issues replayed at install (Cap. 5)
DRY_RUN_PROVISIONAL_BUDGET_USD: Final[float] = 6.0  # the dry run's own tasks, dev default
DRY_RUN_MIN_COMPLETE: Final[int] = 2
EVAL_BLOCK_DROP_POINTS: Final[float] = 10.0
EVAL_GAP_POINTS: Final[int] = 10  # a second installation within 10 points of the pilot
EVAL_MIN_ITEMS: Final[int] = 20
PHASE_CAP_DEFAULT_USD: Final[dict[str, float]] = {
    "planner": 1.50,
    "builder": 3.00,
    "reviewer": 1.50,
}

# --- Infra retries (PRD §Capability 3) ---------------------------------------
INFRA_RETRIES: Final[int] = 3
INFRA_BACKOFF_S: Final[tuple[int, int, int]] = (5, 20, 60)

# --- Protected paths (PRD §Capability 3 · Write boundary) --------------------
PROTECTED_PATHS_DEFAULT: Final[tuple[str, ...]] = (
    ".factory/",
    ".claude/",
    ".github/workflows/",
    "tests/acceptance/",
    "CODEOWNERS",
)
LOCKFILES: Final[tuple[str, ...]] = (
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "go.sum",
    "Gemfile.lock",
)
DEPENDENCY_MANIFESTS: Final[tuple[str, ...]] = ("pyproject.toml", "package.json")
BUILDER_TEST_DIRS: Final[tuple[str, ...]] = ("tests/unit/", "tests/integration/")

# --- Labels and identities (PRD §Capability 1, 2, 5) -------------------------
LABEL_READY: Final[str] = "factory:ready"
LABEL_PLAN_REVIEW: Final[str] = "factory:plan-review"
LABEL_PLAN_APPROVED: Final[str] = "factory:plan-approved"
LABEL_NEEDS_HUMAN: Final[str] = "factory:needs-human"
LABEL_BUDGET: Final[str] = "factory:budget"
LABEL_REVIEW: Final[str] = "factory:review"
BRANCH_PREFIX: Final[str] = "agent/"
EVAL_BRANCH: Final[str] = "eval/scratch"
TRUSTED_ASSOCIATIONS: Final[frozenset[str]] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
WRITE_PERMISSIONS: Final[frozenset[str]] = frozenset({"write", "maintain", "admin"})

# --- PreToolUse hook bundle (PRD §Capability 3; TRD §7.3) -------------------
HOOKS_MOUNT: Final[str] = "/opt/factory/hooks"  # deploy/hooks, read-only in a container
CONTAINER_CODEX_HOME: Final[str] = "/home/agent/.codex"  # CODEX_HOME inside a task container
HOOK_SCRIPT: Final[str] = "factory_pretooluse.py"
CODEX_RULES_FILE: Final[str] = "factory.rules"
HOOK_TIMEOUT_S: Final[int] = 15  # a hook that cannot answer in time is a hook failure
HOOK_DENIAL_PREFIX: Final[str] = "factory-hook: denied:"
PROTECTED_PATHS_ENV: Final[str] = "FACTORY_PROTECTED_PATHS"
PROTECTED_PATHS_SEPARATOR: Final[str] = ";"
