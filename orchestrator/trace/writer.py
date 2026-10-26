"""``TraceWriter``: the only object that writes rows to ``trace.db``.

Each method is named after the row it writes and runs inside one
transaction. Events and the hash chain are added in unit 03.
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from orchestrator import constants
from orchestrator.clock import parse_ts, utc_now
from orchestrator.errors import FactoryDisabledError, TraceError
from orchestrator.ids import new_id
from orchestrator.models.enums import (
    AgentRole,
    ApprovalGate,
    BlockedReason,
    EventKind,
    GateName,
    OutputType,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    ProcessKind,
    RunStatus,
    TaskStatus,
    WorkspaceKind,
)
from orchestrator.trace.chain import append_event
from orchestrator.trace.db import TraceDB

JsonValue = object


def dump_json(value: JsonValue) -> str:
    """Deterministic JSON for stored columns."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _tail(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[-limit:]


class TraceWriter:
    def __init__(self, db: TraceDB) -> None:
        self.db = db

    # --- helpers ------------------------------------------------------------

    def _execute(self, sql: str, params: Sequence[object] = ()) -> int:
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(sql, params)
                return int(cursor.rowcount)
        except sqlite3.IntegrityError as exc:
            raise TraceError(f"trace write refused: {exc}") from exc

    # --- installation -------------------------------------------------------

    def insert_installation(
        self,
        *,
        org: str,
        repo: str,
        factory_version: str,
        config_sha: str,
        per_task_budget_usd: float,
        export_pubkey: str,
        concurrency_n: int = constants.CONCURRENCY_DEFAULT,
        retention_days: int = constants.RETENTION_DAYS_DEFAULT,
        timezone: str = "America/Sao_Paulo",
    ) -> None:
        now = utc_now()
        daily_cap = concurrency_n * constants.DAILY_CAP_MULTIPLIER * per_task_budget_usd
        self._execute(
            "INSERT INTO installation (id, org, repo, factory_version, config_sha, concurrency_n,"
            " per_task_budget_usd, daily_cap_usd, retention_days, export_pubkey, timezone,"
            " created_at, updated_at, install_id)"
            " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                org,
                repo,
                factory_version,
                config_sha,
                concurrency_n,
                per_task_budget_usd,
                daily_cap,
                retention_days,
                export_pubkey,
                timezone,
                now,
                now,
                new_id(),
            ),
        )

    # --- events -------------------------------------------------------------

    def write_event(
        self,
        kind: EventKind,
        actor: str,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        phase_id: str | None = None,
        parent_seq: int | None = None,
        payload: object = None,
        tokens: int | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> int:
        """Append one hash-chained event row and return its ``seq`` (TRD §4.3)."""
        return append_event(
            self.db,
            kind=kind,
            actor=actor,
            task_id=task_id,
            run_id=run_id,
            phase_id=phase_id,
            parent_seq=parent_seq,
            payload=payload,
            tokens=tokens,
            started_at=started_at,
            ended_at=ended_at,
        )

    def get_installation(self) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM installation WHERE id = 1")

    def set_concurrency(self, n: int) -> None:
        self._execute(
            "UPDATE installation SET concurrency_n = ?, updated_at = ? WHERE id = 1", (n, utc_now())
        )

    def set_daily_cap(self, daily_cap_usd: float) -> None:
        """A ceiling set directly: an eval replay's scratch trace takes items x the per-task
        budget (user decision 2026-10-25). The production row keeps the trigger's N x 3 x
        budget, which any later change of N or the budget restores."""
        self._execute(
            "UPDATE installation SET daily_cap_usd = ?, updated_at = ? WHERE id = 1",
            (daily_cap_usd, utc_now()),
        )

    def set_factory_enabled(self, enabled: bool) -> None:
        self._execute(
            "UPDATE installation SET factory_enabled = ?, updated_at = ? WHERE id = 1",
            (1 if enabled else 0, utc_now()),
        )

    def set_admissions_paused(self, paused: bool) -> None:
        self._execute(
            "UPDATE installation SET admissions_paused = ?, updated_at = ? WHERE id = 1",
            (1 if paused else 0, utc_now()),
        )

    def admissions_paused(self) -> bool:
        row = self.get_installation()
        return bool(row["admissions_paused"]) if row is not None else False

    def set_factory_version(self, version: str) -> None:
        self._execute(
            "UPDATE installation SET factory_version = ?, updated_at = ? WHERE id = 1",
            (version, utc_now()),
        )

    def set_export_pubkey(self, pubkey: str) -> None:
        self._execute(
            "UPDATE installation SET export_pubkey = ?, updated_at = ? WHERE id = 1",
            (pubkey, utc_now()),
        )

    def factory_enabled(self) -> bool:
        """``installation.factory_enabled``; unreadable or missing counts as 0 (TRD §11)."""
        try:
            row = self.get_installation()
        except sqlite3.Error:
            return False
        return row is not None and bool(row["factory_enabled"])

    def require_enabled(self) -> None:
        """Called before every phase start: the kill-switch is checked by code."""
        if not self.factory_enabled():
            raise FactoryDisabledError(
                "FACTORY_ENABLED is 0; no phase starts until a person re-enables the factory"
            )

    def running_task_ids(self) -> list[str]:
        rows = self.db.fetchall(
            "SELECT task_id FROM tasks WHERE status = 'running' ORDER BY created_at, task_id"
        )
        return [str(r["task_id"]) for r in rows]

    def count_open_processes(self) -> int:
        value = self.db.scalar("SELECT COUNT(*) FROM processes WHERE ended_at IS NULL")
        return int(value or 0)

    def set_task_budget(self, task_id: str, budget_usd: float) -> None:
        self._execute(
            "UPDATE tasks SET budget_usd = ?, updated_at = ? WHERE task_id = ?",
            (budget_usd, utc_now(), task_id),
        )

    def latest_budget_stop(self, task_id: str) -> dict[str, object] | None:
        """The last ``budget_stop`` payload of a task (the spend comment reads it)."""
        row = self.db.fetchone(
            "SELECT payload_json FROM events WHERE task_id = ? AND kind = 'budget_stop'"
            " ORDER BY seq DESC LIMIT 1",
            (task_id,),
        )
        if row is None:
            return None
        loaded = json.loads(row["payload_json"])
        return loaded if isinstance(loaded, dict) else None

    def last_build_commit(self, task_id: str) -> str | None:
        """The latest build commit handed off (the plan's handoff carries no message)."""
        row = self.db.fetchone(
            "SELECT payload_json FROM events WHERE task_id = ? AND kind = 'handoff'"
            " AND payload_json LIKE ? ORDER BY seq DESC LIMIT 1",
            (task_id, '%"message":%'),
        )
        if row is None:
            return None
        loaded = json.loads(row["payload_json"])
        commit = loaded.get("commit") if isinstance(loaded, dict) else None
        return commit if isinstance(commit, str) else None

    def awaiting_logged(self, task_id: str, bound_sha: str) -> bool:
        """Whether a plan-review wait for this spec sha was announced (restart idempotence)."""
        row = self.db.fetchone(
            "SELECT 1 FROM events WHERE task_id = ? AND kind = 'log'"
            " AND payload_json LIKE ? AND payload_json LIKE ? LIMIT 1",
            (task_id, '%"awaiting":%', f'%"bound_sha":"{bound_sha}"%'),
        )
        return row is not None

    # --- idempotency keys for the write endpoints (TRD §6.1) ------------------------------

    def recall_response(self, *, key: str, route: str, actor: str) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT * FROM idempotency_keys WHERE key = ? AND route = ? AND actor = ?",
            (key, route, actor),
        )

    def remember_response(
        self,
        *,
        key: str,
        route: str,
        actor: str,
        request_sha256: str,
        status: int,
        body_json: str,
    ) -> None:
        self._execute(
            "INSERT INTO idempotency_keys (key, route, actor, request_sha256, status,"
            " response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(key, route, actor) DO NOTHING",
            (key, route, actor, request_sha256, status, body_json, utc_now()),
        )

    # --- KPIs and the audit queue (PRD §Metrics; TRD §4.2) -------------------------------

    def upsert_kpi(
        self,
        day: str,
        metric: str,
        *,
        value: float | None,
        numerator: float | None,
        denominator: float | None,
    ) -> None:
        self._execute(
            "INSERT INTO kpi_daily (day, metric, value, numerator, denominator)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(day, metric) DO UPDATE SET"
            " value = excluded.value, numerator = excluded.numerator,"
            " denominator = excluded.denominator",
            (day, metric, value, numerator, denominator),
        )

    def kpis_for_day(self, day: str) -> dict[str, sqlite3.Row]:
        rows = self.db.fetchall("SELECT * FROM kpi_daily WHERE day = ? ORDER BY metric", (day,))
        return {str(r["metric"]): r for r in rows}

    def merged_in_month(self, month: str) -> list[sqlite3.Row]:
        """Tasks merged in the month (``YYYY-MM`` prefix of the log) with no audit draw yet."""
        return self.db.fetchall(
            "SELECT DISTINCT t.task_id FROM tasks t JOIN events e ON e.task_id = t.task_id"
            " WHERE t.status = 'merged' AND e.kind = 'log' AND e.payload_json LIKE ?"
            " AND substr(e.created_at, 1, 7) = ? AND NOT EXISTS (SELECT 1 FROM audit_decisions a"
            " WHERE a.task_id = t.task_id) ORDER BY t.task_id",
            ('%"merged":%', month),
        )

    def insert_audit_decision(
        self, *, task_id: str, pr_number: int, month: str, stratum: str, sampled: bool
    ) -> bool:
        """One row per task and month (the sampler's draw); False when it exists."""
        rows = self._execute(
            "INSERT INTO audit_decisions (id, task_id, pr_number, month, stratum, sampled)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(task_id, month) DO NOTHING",
            (new_id(), task_id, pr_number, month, stratum, 1 if sampled else 0),
        )
        return rows > 0

    def decide_audit(self, *, task_id: str, month: str, decision: str, reason: str, by: str) -> int:
        """The tech lead's decision on a drawn task: an ``audit`` event and the row (once)."""
        row = self.db.fetchone(
            "SELECT id, stratum, pr_number, decision FROM audit_decisions"
            " WHERE task_id = ? AND month = ?",
            (task_id, month),
        )
        if row is None:
            raise TraceError(f"{task_id} was not drawn for {month}")
        if row["decision"] is not None:
            raise TraceError(f"{task_id} is already decided for {month}")
        seq = self.write_event(
            EventKind.AUDIT,
            by,
            task_id=task_id,
            payload={
                "pr": int(row["pr_number"]),
                "stratum": str(row["stratum"]),
                "month": month,
                "decision": decision,
                "reason": reason,
            },
        )
        self._execute(
            "UPDATE audit_decisions SET decision = ?, reason = ?, decided_by = ?, decided_at = ?,"
            " event_seq = ? WHERE id = ?",
            (decision, reason, by, utc_now(), seq, row["id"]),
        )
        return seq

    def audit_queue(self) -> list[sqlite3.Row]:
        return self.db.fetchall("SELECT * FROM v_audit_queue ORDER BY month, task_id")

    # --- eval sets and runs (TRD §4.2) --------------------------------------------------

    def create_eval_set(
        self, *, name: str, repo: str, reference_pass_rate: float | None = None
    ) -> str:
        set_id = new_id()
        self._execute(
            "INSERT INTO eval_sets (id, name, repo, reference_pass_rate, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (set_id, name, repo, reference_pass_rate, utc_now()),
        )
        return set_id

    def eval_set(self, set_id: str) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM eval_sets WHERE id = ?", (set_id,))

    def set_eval_reference(self, set_id: str, reference_pass_rate: float | None) -> None:
        """The operator-entered pass rate a set's gap is measured against (TRD §4.2)."""
        self._execute(
            "UPDATE eval_sets SET reference_pass_rate = ? WHERE id = ?",
            (reference_pass_rate, set_id),
        )

    def find_eval_set(self, name: str) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT * FROM eval_sets WHERE name = ? ORDER BY created_at DESC LIMIT 1", (name,)
        )

    def add_eval_item(
        self, set_id: str, *, issue_number: int, eval_class: str, merged_pr: int | None = None
    ) -> str:
        """Idempotent on (set, issue); returns the item id."""
        self._execute(
            "INSERT INTO eval_items (id, set_id, issue_number, class, merged_pr)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(set_id, issue_number) DO NOTHING",
            (new_id(), set_id, issue_number, eval_class, merged_pr),
        )
        row = self.db.fetchone(
            "SELECT id FROM eval_items WHERE set_id = ? AND issue_number = ?",
            (set_id, issue_number),
        )
        assert row is not None
        return str(row["id"])

    def eval_items(self, set_id: str) -> list[sqlite3.Row]:
        return self.db.fetchall(
            "SELECT * FROM eval_items WHERE set_id = ? ORDER BY issue_number", (set_id,)
        )

    def start_eval_run(
        self,
        *,
        set_id: str,
        factory_version: str,
        config_sha: str,
        trigger: str,
        trigger_ref: str | None,
        baseline_run_id: str | None,
    ) -> str:
        run_id = new_id()
        self._execute(
            "INSERT INTO eval_runs (id, set_id, factory_version, config_sha, trigger, trigger_ref,"
            " baseline_run_id, started_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')",
            (
                run_id,
                set_id,
                factory_version,
                config_sha,
                trigger,
                trigger_ref,
                baseline_run_id,
                utc_now(),
            ),
        )
        return run_id

    def record_eval_result(
        self,
        run_id: str,
        *,
        item_id: str,
        passed: bool,
        cost_usd: float | None,
        duration_s: int | None,
        failure_reason: str | None,
    ) -> None:
        self._execute(
            "INSERT INTO eval_results"
            " (run_id, item_id, passed, cost_usd, duration_s, failure_reason)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, item_id, 1 if passed else 0, cost_usd, duration_s, failure_reason),
        )

    def finish_eval_run(
        self,
        run_id: str,
        *,
        status: str,
        pass_rate: float | None = None,
        per_class_json: str | None = None,
        delta_points: float | None = None,
        blocked: bool | None = None,
    ) -> None:
        self._execute(
            "UPDATE eval_runs SET ended_at = ?, status = ?, pass_rate = ?, per_class_json = ?,"
            " delta_points = ?, blocked = ? WHERE id = ?",
            (
                utc_now(),
                status,
                pass_rate,
                per_class_json,
                delta_points,
                None if blocked is None else (1 if blocked else 0),
                run_id,
            ),
        )

    def get_eval_run(self, run_id: str) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM eval_runs WHERE id = ?", (run_id,))

    def latest_complete_eval_run(self, set_id: str) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT * FROM eval_runs WHERE set_id = ? AND status = 'complete'"
            " ORDER BY started_at DESC, id DESC LIMIT 1",
            (set_id,),
        )

    def eval_results(self, run_id: str) -> list[sqlite3.Row]:
        return self.db.fetchall(
            "SELECT r.*, i.issue_number, i.class FROM eval_results r"
            " JOIN eval_items i ON i.id = r.item_id WHERE r.run_id = ? ORDER BY i.issue_number",
            (run_id,),
        )

    def set_per_task_budget(self, per_task_budget_usd: float, *, mode: str) -> None:
        self._execute(
            "UPDATE installation SET per_task_budget_usd = ?, budget_mode = ?, updated_at = ?"
            " WHERE id = 1",
            (per_task_budget_usd, mode, utc_now()),
        )

    # --- configuration ------------------------------------------------------

    def load_config_version(
        self,
        *,
        config_sha: str,
        main_commit: str,
        roster_json: str,
        risk_yaml: str,
        review_policy_yaml: str,
        protected_paths_json: str,
        quality_commands_json: str,
        vendors_json: str,
    ) -> bool:
        """Insert a loaded ``.factory/`` tree; return False when the sha is already known."""
        rows = self._execute(
            "INSERT INTO config_versions (config_sha, main_commit, roster_json, risk_yaml,"
            " review_policy_yaml, protected_paths_json, quality_commands_json, vendors_json,"
            " loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(config_sha) DO NOTHING",
            (
                config_sha,
                main_commit,
                roster_json,
                risk_yaml,
                review_policy_yaml,
                protected_paths_json,
                quality_commands_json,
                vendors_json,
                utc_now(),
            ),
        )
        return rows == 1

    # --- github intake ------------------------------------------------------

    def record_github_event(
        self, *, source: str, event_type: str, github_event_id: str, issue_number: int | None
    ) -> bool:
        """Insert a delivery once; return False when ``(event_type, id)`` was already seen."""
        rows = self._execute(
            "INSERT INTO github_events (source, github_event_id, event_type, issue_number,"
            " received_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(event_type, github_event_id) DO NOTHING",
            (source, github_event_id, event_type, issue_number, utc_now()),
        )
        return rows == 1

    def mark_github_event_processed(self, *, event_type: str, github_event_id: str) -> None:
        self._execute(
            "UPDATE github_events SET processed_at = ?"
            " WHERE event_type = ? AND github_event_id = ?",
            (utc_now(), event_type, github_event_id),
        )

    def get_poll_cursor(self, name: str) -> str | None:
        value = self.db.scalar("SELECT value FROM poll_cursors WHERE name = ?", (name,))
        return None if value is None else str(value)

    def set_poll_cursor(self, name: str, value: str) -> None:
        self._execute(
            "INSERT INTO poll_cursors (name, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (name, value, utc_now()),
        )

    def open_pull_requests(self) -> list[sqlite3.Row]:
        return self.db.fetchall(
            "SELECT task_id, pr_number, pr_head_sha, branch FROM tasks"
            " WHERE status = 'pr_open' AND pr_number IS NOT NULL ORDER BY pr_number"
        )

    def set_last_poll(self, at: str) -> None:
        self._last_poll_at = at

    def last_poll_at(self) -> str | None:
        return getattr(self, "_last_poll_at", None)

    # --- permission cache (TRD §6.2) ------------------------------------------

    def cache_permission(
        self, login: str, permission: str, *, codeowner_paths: list[str] | None = None
    ) -> None:
        self._execute(
            "INSERT INTO user_permissions (login, permission, codeowner_paths_json, checked_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(login) DO UPDATE SET"
            " permission = excluded.permission,"
            " codeowner_paths_json = excluded.codeowner_paths_json,"
            " checked_at = excluded.checked_at",
            (login, permission, dump_json(codeowner_paths or []), utc_now()),
        )

    def cached_permission(self, login: str, *, ttl_s: int) -> str | None:
        row = self.db.fetchone(
            "SELECT permission, checked_at FROM user_permissions WHERE login = ?", (login,)
        )
        if row is None:
            return None
        age = (datetime.now(UTC) - parse_ts(str(row["checked_at"]))).total_seconds()
        return str(row["permission"]) if age <= ttl_s else None

    # --- queue and lifecycle (PRD Cap. 1) ------------------------------------------

    def count_running(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM tasks WHERE status = 'running'") or 0)

    def oldest_queued(self) -> str | None:
        value = self.db.scalar(
            "SELECT task_id FROM tasks WHERE status = 'queued'"
            " ORDER BY created_at, issue_number LIMIT 1"
        )
        return None if value is None else str(value)

    def queue_position(self, task_id: str) -> int:
        """How many queued tasks were admitted before this one (label order)."""
        value = self.db.scalar(
            "SELECT COUNT(*) FROM tasks WHERE status = 'queued' AND created_at <"
            " (SELECT created_at FROM tasks WHERE task_id = ?)",
            (task_id,),
        )
        return int(value or 0)

    def requeue_task(self, task_id: str) -> None:
        """A cancelled task re-labelled factory:ready: a new attempt on the kept branch."""
        self._execute(
            "UPDATE tasks SET status = 'queued', blocked_reason = NULL, attempt = attempt + 1,"
            " updated_at = ? WHERE task_id = ?",
            (utc_now(), task_id),
        )

    def last_completed_phase(self, task_id: str) -> str | None:
        value = self.db.scalar(
            "SELECT p.name FROM phases p JOIN runs r ON r.run_id = p.run_id"
            " WHERE r.task_id = ? AND p.status = 'pass'"
            " ORDER BY p.ended_at DESC, p.seq DESC LIMIT 1",
            (task_id,),
        )
        return None if value is None else str(value)

    def close_open_work(self, task_id: str, *, error: str) -> None:
        """Fail every running phase and run of a task and end its processes."""
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE processes SET ended_at = ? WHERE ended_at IS NULL AND run_id IN"
                " (SELECT run_id FROM runs WHERE task_id = ?)",
                (now, task_id),
            )
            conn.execute(
                "UPDATE phases SET status = 'fail', ended_at = ?, error = ?"
                " WHERE status = 'running'"
                " AND run_id IN (SELECT run_id FROM runs WHERE task_id = ?)",
                (now, error, task_id),
            )
            conn.execute(
                "UPDATE runs SET status = 'fail', ended_at = ?"
                " WHERE status = 'running' AND task_id = ?",
                (now, task_id),
            )

    # --- tasks --------------------------------------------------------------

    def insert_task(
        self,
        *,
        task_id: str,
        issue_number: int,
        title: str | None,
        branch: str,
        workspace_kind: WorkspaceKind,
        budget_usd: float,
        admitted_by: str,
        config_sha: str,
        status: TaskStatus = TaskStatus.QUEUED,
    ) -> bool:
        """Idempotent on ``task_id``; return False when the task already exists."""
        now = utc_now()
        rows = self._execute(
            "INSERT INTO tasks (task_id, issue_number, title, status, branch, workspace_kind,"
            " budget_usd, admitted_by, config_sha, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(task_id) DO NOTHING",
            (
                task_id,
                issue_number,
                title,
                str(status),
                branch,
                str(workspace_kind),
                budget_usd,
                admitted_by,
                config_sha,
                now,
                now,
            ),
        )
        return rows == 1

    def get_task(self, task_id: str) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM tasks WHERE task_id = ?", (task_id,))

    def set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        blocked_reason: BlockedReason | None = None,
    ) -> None:
        if status is TaskStatus.BLOCKED and blocked_reason is None:
            raise TraceError("a blocked task must name its blocked_reason")
        if status is not TaskStatus.BLOCKED and blocked_reason is not None:
            raise TraceError("blocked_reason is only valid with status 'blocked'")
        self._execute(
            "UPDATE tasks SET status = ?, blocked_reason = ?, updated_at = ? WHERE task_id = ?",
            (
                str(status),
                None if blocked_reason is None else str(blocked_reason),
                utc_now(),
                task_id,
            ),
        )

    def set_task_spec(self, task_id: str, *, spec_path: str, spec_sha: str) -> None:
        """Bind the spec; a non-empty sha also stamps ``spec_committed_at`` (approvals)."""
        now = utc_now()
        self._execute(
            "UPDATE tasks SET spec_path = ?, spec_sha = ?, updated_at = ?,"
            " spec_committed_at = CASE WHEN ? = '' THEN spec_committed_at ELSE ? END"
            " WHERE task_id = ?",
            (spec_path, spec_sha, now, spec_sha, now, task_id),
        )

    def set_task_pr(self, task_id: str, *, pr_number: int, head_sha: str | None) -> None:
        self._execute(
            "UPDATE tasks SET pr_number = ?, pr_head_sha = ?, updated_at = ? WHERE task_id = ?",
            (pr_number, head_sha, utc_now(), task_id),
        )

    def set_task_risk(
        self,
        task_id: str,
        *,
        risk: str,
        score: float,
        overridden_by: str | None = None,
        governed_path: str | None = None,
    ) -> None:
        self._execute(
            "UPDATE tasks SET risk = ?, risk_score = ?, risk_overridden_by = ?,"
            " governed_path = ?, updated_at = ? WHERE task_id = ?",
            (risk, score, overridden_by, governed_path, utc_now(), task_id),
        )

    # --- approvals (PRD Cap. 2; TRD §6.2) ------------------------------------------

    def record_approval(
        self,
        *,
        task_id: str,
        gate: ApprovalGate,
        actor_login: str,
        bound_sha: str,
        github_event_id: str,
        phase_id: str | None = None,
        governed_path: str | None = None,
        codeowner: bool | None = None,
        reason: str | None = None,
    ) -> str | None:
        """Insert the approval row; ``None`` when one already covers (task, gate, sha)."""
        approval_id = new_id()
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO approvals (id, task_id, phase_id, gate, actor_login, bound_sha,"
                    " governed_path, codeowner, reason, github_label, github_event_id,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        approval_id,
                        task_id,
                        phase_id,
                        str(gate),
                        actor_login,
                        bound_sha,
                        governed_path,
                        None if codeowner is None else int(codeowner),
                        reason,
                        constants.LABEL_PLAN_APPROVED,
                        github_event_id,
                        utc_now(),
                    ),
                )
        except sqlite3.IntegrityError:
            return None
        return approval_id

    def approval_for(
        self, task_id: str, *, gate: ApprovalGate, bound_sha: str
    ) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT * FROM approvals WHERE task_id = ? AND gate = ? AND bound_sha = ?",
            (task_id, str(gate), bound_sha),
        )

    def set_task_attempt(self, task_id: str, attempt: int) -> None:
        self._execute(
            "UPDATE tasks SET attempt = ?, updated_at = ? WHERE task_id = ?",
            (attempt, utc_now(), task_id),
        )

    # --- runs and phases ----------------------------------------------------

    def start_run(
        self,
        *,
        task_id: str,
        attempt: int,
        factory_version: str,
        config_sha: str,
        workflow: str = "issue_to_pr",
    ) -> str:
        run_id = new_id()
        self._execute(
            "INSERT INTO runs (run_id, task_id, attempt, workflow, factory_version, config_sha,"
            " status, started_at) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
            (run_id, task_id, attempt, workflow, factory_version, config_sha, utc_now()),
        )
        return run_id

    def run_for_attempt(self, task_id: str, attempt: int) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT * FROM runs WHERE task_id = ? AND attempt = ?", (task_id, attempt)
        )

    def finish_run(self, run_id: str, status: RunStatus) -> None:
        """Close a run and roll phase tokens and cost up into it."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, ended_at = ?,"
                " tokens_in = (SELECT COALESCE(SUM(tokens_in), 0) FROM phases WHERE run_id = ?),"
                " tokens_out = (SELECT COALESCE(SUM(tokens_out), 0) FROM phases WHERE run_id = ?),"
                " total_cost_usd = (SELECT COALESCE(SUM(cost_usd), 0) FROM phases WHERE run_id = ?)"
                " WHERE run_id = ?",
                (str(status), utc_now(), run_id, run_id, run_id, run_id),
            )

    def spent_since(self, since_ts: str) -> float:
        """Sum of phase costs started at or after ``since_ts`` (the daily cap input)."""
        value = self.db.scalar(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM phases WHERE started_at >= ?", (since_ts,)
        )
        return float(value or 0.0)

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM runs WHERE run_id = ?", (run_id,))

    def start_phase(
        self,
        *,
        run_id: str,
        seq: int,
        name: PhaseName,
        kind: PhaseKind,
        owner: str,
        timeout_s: int,
        attempt: int = 1,
        phase_cap_usd: float | None = None,
        harness: str | None = None,
        harness_version: str | None = None,
        model_id: str | None = None,
        session_id: str | None = None,
        virtual_key_id: str | None = None,
    ) -> str:
        phase_id = new_id()
        self._execute(
            "INSERT INTO phases (phase_id, run_id, seq, name, kind, owner, status, attempt,"
            " harness, harness_version, model_id, session_id, virtual_key_id, timeout_s,"
            " phase_cap_usd, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                phase_id,
                run_id,
                seq,
                str(name),
                str(kind),
                owner,
                attempt,
                harness,
                harness_version,
                model_id,
                session_id,
                virtual_key_id,
                timeout_s,
                phase_cap_usd,
                utc_now(),
            ),
        )
        return phase_id

    def set_phase_key(self, phase_id: str, key_id: str) -> None:
        """The proxy's hashed token for the phase's virtual key; never the secret."""
        self._execute("UPDATE phases SET virtual_key_id = ? WHERE phase_id = ?", (key_id, phase_id))

    def session_cost_usd(self, session_id: str) -> float:
        """What earlier phases already booked for one harness session."""
        row = self.db.fetchone(
            "SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM phases WHERE session_id = ?",
            (session_id,),
        )
        return float(row["spent"]) if row is not None else 0.0

    def finish_phase(
        self,
        phase_id: str,
        status: PhaseStatus,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tokens_cache_read: int = 0,
        tokens_cache_write: int = 0,
        cost_usd: float = 0.0,
        retries: int = 0,
        session_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Close a phase, record its usage, and add its cost to the task."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE phases SET status = ?, ended_at = ?, tokens_in = ?, tokens_out = ?,"
                " tokens_cache_read = ?, tokens_cache_write = ?, cost_usd = ?, retries = ?,"
                " session_id = COALESCE(?, session_id), error = ? WHERE phase_id = ?",
                (
                    str(status),
                    utc_now(),
                    tokens_in,
                    tokens_out,
                    tokens_cache_read,
                    tokens_cache_write,
                    cost_usd,
                    retries,
                    session_id,
                    error,
                    phase_id,
                ),
            )
            conn.execute(
                "UPDATE tasks SET spent_usd = spent_usd + ?, updated_at = ? WHERE task_id ="
                " (SELECT r.task_id FROM runs r JOIN phases p ON p.run_id = r.run_id"
                "  WHERE p.phase_id = ?)",
                (cost_usd, utc_now(), phase_id),
            )

    def get_phase(self, phase_id: str) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM phases WHERE phase_id = ?", (phase_id,))

    # --- gates, envelopes, processes ---------------------------------------

    def write_gate_result(
        self,
        *,
        run_id: str,
        phase_id: str,
        attempt: int,
        gate: GateName,
        passed: bool,
        checks: Sequence[Mapping[str, object]],
        violations: Sequence[Mapping[str, object]] | None = None,
        command: str | None = None,
        exit_code: int | None = None,
        output_tail: str | None = None,
    ) -> str:
        gate_id = new_id()
        self._execute(
            "INSERT INTO gate_results (id, run_id, phase_id, attempt, gate, passed, checks_json,"
            " violations_json, command, exit_code, output_tail, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                gate_id,
                run_id,
                phase_id,
                attempt,
                str(gate),
                1 if passed else 0,
                dump_json(list(checks)),
                None if violations is None else dump_json(list(violations)),
                command,
                exit_code,
                _tail(output_tail, constants.OUTPUT_TAIL_CHARS),
                utc_now(),
            ),
        )
        return gate_id

    def write_envelope(
        self,
        *,
        run_id: str,
        phase_id: str,
        agent: AgentRole,
        output_type: OutputType,
        payload_json: str | None,
        valid: bool,
        attempt: int,
        violations: Sequence[Mapping[str, object]] | None = None,
    ) -> str:
        envelope_id = new_id()
        self._execute(
            "INSERT INTO envelopes (envelope_id, run_id, phase_id, agent, output_type,"
            " payload_json, valid, violations_json, attempt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                envelope_id,
                run_id,
                phase_id,
                str(agent),
                str(output_type),
                payload_json,
                1 if valid else 0,
                None if violations is None else dump_json(list(violations)),
                attempt,
                utc_now(),
            ),
        )
        return envelope_id

    # --- readers for later phases ------------------------------------------------

    def last_phase_seq(self, run_id: str) -> int:
        value = self.db.scalar(
            "SELECT COALESCE(MAX(seq), 0) FROM phases WHERE run_id = ?", (run_id,)
        )
        return int(value or 0)

    def gate_results_for_run(self, run_id: str) -> list[sqlite3.Row]:
        return self.db.fetchall(
            "SELECT * FROM gate_results WHERE run_id = ? ORDER BY created_at, rowid", (run_id,)
        )

    def latest_envelope(
        self, run_id: str, output_type: OutputType, *, valid_only: bool = True
    ) -> str | None:
        """The newest envelope text of that type in the run (valid ones only by default)."""
        row = self.db.fetchone(
            "SELECT payload_json FROM envelopes WHERE run_id = ? AND output_type = ?"
            " AND (valid = 1 OR ? = 0) ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (run_id, str(output_type), 1 if valid_only else 0),
        )
        if row is None or row["payload_json"] is None:
            return None
        return str(row["payload_json"])

    def latest_log_payload(self, run_id: str, key: str) -> dict[str, object] | None:
        """The ``key`` object of the newest log event in the run that carries it."""
        row = self.db.fetchone(
            "SELECT payload_json FROM events WHERE run_id = ? AND kind = 'log'"
            " AND payload_json LIKE ? ORDER BY seq DESC LIMIT 1",
            (run_id, f'%"{key}"%'),
        )
        if row is None or row["payload_json"] is None:
            return None
        payload = json.loads(row["payload_json"])
        value = payload.get(key) if isinstance(payload, dict) else None
        return value if isinstance(value, dict) else None

    def open_phase(self, task_id: str, name: PhaseName) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT p.* FROM phases p JOIN runs r ON r.run_id = p.run_id"
            " WHERE r.task_id = ? AND p.name = ? AND p.status = 'running'"
            " ORDER BY p.started_at DESC, p.seq DESC LIMIT 1",
            (task_id, str(name)),
        )

    def finish_open_phase(
        self, task_id: str, name: PhaseName, status: PhaseStatus, *, error: str | None = None
    ) -> str | None:
        """Close the running phase of that name, with its end event; ``None`` when none is open."""
        row = self.open_phase(task_id, name)
        if row is None:
            return None
        phase_id = str(row["phase_id"])
        self.finish_phase(phase_id, status, error=error)
        self.write_event(
            EventKind.PHASE_END,
            "orchestrator",
            task_id=task_id,
            run_id=str(row["run_id"]),
            phase_id=phase_id,
            payload={"status": str(status), "error": error},
        )
        return phase_id

    def latest_control(self, task_id: str, action: str) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT seq, created_at, payload_json FROM events WHERE task_id = ?"
            " AND kind = 'control' AND payload_json LIKE ? ORDER BY seq DESC LIMIT 1",
            (task_id, f'%"action":"{action}"%'),
        )

    def latest_handoff_seq(self, task_id: str, key: str) -> int | None:
        value = self.db.scalar(
            "SELECT MAX(seq) FROM events WHERE task_id = ? AND kind = 'handoff'"
            " AND payload_json LIKE ?",
            (task_id, f'%"{key}":%'),
        )
        return None if value is None else int(value)

    def count_controls(self, task_id: str, action: str, *, since_seq: int = 0) -> int:
        value = self.db.scalar(
            "SELECT COUNT(*) FROM events WHERE task_id = ? AND kind = 'control'"
            " AND payload_json LIKE ? AND seq > ?",
            (task_id, f'%"action":"{action}"%', since_seq),
        )
        return int(value or 0)

    def phases_for_run(self, run_id: str) -> list[sqlite3.Row]:
        return self.db.fetchall("SELECT * FROM phases WHERE run_id = ? ORDER BY seq", (run_id,))

    def task_for_pr(self, pr_number: int) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM tasks WHERE pr_number = ?", (pr_number,))

    def task_for_branch(self, branch: str) -> sqlite3.Row | None:
        return self.db.fetchone(
            "SELECT * FROM tasks WHERE branch = ? ORDER BY created_at DESC LIMIT 1", (branch,)
        )

    def end_orphan_processes(self) -> int:
        """End every process row without an end (a previous orchestrator died)."""
        return self._execute(
            "UPDATE processes SET ended_at = ? WHERE ended_at IS NULL", (utc_now(),)
        )

    def start_process(
        self,
        *,
        run_id: str,
        phase_id: str | None,
        kind: ProcessKind,
        name: str,
        pid: int | None = None,
        container_id: str | None = None,
        command: str | None = None,
    ) -> str:
        process_id = new_id()
        now = utc_now()
        self._execute(
            "INSERT INTO processes (id, run_id, phase_id, kind, name, pid, container_id, command,"
            " started_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (process_id, run_id, phase_id, str(kind), name, pid, container_id, command, now, now),
        )
        return process_id

    def set_process_pid(self, process_id: str, pid: int) -> None:
        self._execute(
            "UPDATE processes SET pid = ?, last_event_at = ? WHERE id = ?",
            (pid, utc_now(), process_id),
        )

    def touch_process(self, process_id: str) -> None:
        self._execute(
            "UPDATE processes SET last_event_at = ? WHERE id = ?", (utc_now(), process_id)
        )

    def end_process(self, process_id: str, *, exit_code: int | None) -> None:
        self._execute(
            "UPDATE processes SET ended_at = ?, exit_code = ? WHERE id = ?",
            (utc_now(), exit_code, process_id),
        )

    def get_process(self, process_id: str) -> sqlite3.Row | None:
        return self.db.fetchone("SELECT * FROM processes WHERE id = ?", (process_id,))
