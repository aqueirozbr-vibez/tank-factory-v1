"""Unit 02: trace schema, migration runner and the single writer."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestrator.errors import TraceError
from orchestrator.models.enums import (
    AgentRole,
    BlockedReason,
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
from orchestrator.trace import TraceDB, TraceWriter, default_migrations_dir
from orchestrator.trace.writer import dump_json

VENDORS_OK = dump_json({"planner": "anthropic", "builder": "anthropic", "reviewer": "openai"})
VENDORS_SAME = dump_json({"planner": "anthropic", "builder": "openai", "reviewer": "openai"})


@pytest.fixture
def db(tmp_path: Path) -> Iterator[TraceDB]:
    database = TraceDB.open(tmp_path / "trace.db")
    yield database
    database.close()


@pytest.fixture
def writer(db: TraceDB) -> TraceWriter:
    w = TraceWriter(db)
    w.load_config_version(
        config_sha="cfg1",
        main_commit="abc",
        roster_json="{}",
        risk_yaml="",
        review_policy_yaml="",
        protected_paths_json="[]",
        quality_commands_json="{}",
        vendors_json=VENDORS_OK,
    )
    w.insert_installation(
        org="acme",
        repo="atlas-api",
        factory_version="0.1.0",
        config_sha="cfg1",
        per_task_budget_usd=6.0,
        export_pubkey="pk",
    )
    return w


def _task(writer: TraceWriter, issue: int = 418) -> str:
    task_id = f"acme/atlas-api#{issue}"
    assert writer.insert_task(
        task_id=task_id,
        issue_number=issue,
        title="Invoice due date",
        branch=f"agent/{issue}",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    return task_id


# --- migrations and pragmas -------------------------------------------------


def test_migration_applies_once_and_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "trace.db"
    first = TraceDB.open(path)
    assert first.applied_migrations() == [
        "0001_init",
        "0002_installation_install_id",
        "0003_poll_cursors",
        "0004_task_approval_binding",
        "0005_idempotency",
    ]
    first.close()
    second = TraceDB.open(path)
    assert second.applied_migrations() == [
        "0001_init",
        "0002_installation_install_id",
        "0003_poll_cursors",
        "0004_task_approval_binding",
        "0005_idempotency",
    ]
    assert second.scalar("SELECT COUNT(*) FROM schema_migrations") == 5
    second.close()


def test_pragmas(db: TraceDB) -> None:
    assert db.scalar("PRAGMA journal_mode") == "wal"
    assert db.scalar("PRAGMA foreign_keys") == 1
    assert db.scalar("PRAGMA busy_timeout") == 5000


def test_migrations_dir_resolves_to_repo() -> None:
    assert (default_migrations_dir() / "0001_init.sql").is_file()


def test_bad_migration_name_is_refused(tmp_path: Path) -> None:
    bad_dir = tmp_path / "migrations"
    bad_dir.mkdir()
    (bad_dir / "init.sql").write_text("CREATE TABLE x (a);", encoding="utf-8")
    with pytest.raises(TraceError, match="NNNN_short_name"):
        TraceDB.open(tmp_path / "t.db", migrations_dir=bad_dir)


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path: Path) -> None:
    bad_dir = tmp_path / "migrations"
    bad_dir.mkdir()
    (bad_dir / "0001_broken.sql").write_text(
        "CREATE TABLE ok_table (a);\nCREATE TABLE broken (;", encoding="utf-8"
    )
    with pytest.raises(TraceError, match="0001_broken"):
        TraceDB.open(tmp_path / "t.db", migrations_dir=bad_dir)
    conn = sqlite3.connect(tmp_path / "t.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "ok_table" not in tables
    assert "broken" not in tables


# --- installation -----------------------------------------------------------


def test_daily_cap_is_n_times_three_times_budget(writer: TraceWriter) -> None:
    row = writer.get_installation()
    assert row is not None
    assert row["daily_cap_usd"] == pytest.approx(3 * 3 * 6.0)
    writer.set_concurrency(5)
    row = writer.get_installation()
    assert row is not None
    assert row["concurrency_n"] == 5
    assert row["daily_cap_usd"] == pytest.approx(5 * 3 * 6.0)
    writer.set_per_task_budget(10.0, mode="median")
    row = writer.get_installation()
    assert row is not None
    assert row["daily_cap_usd"] == pytest.approx(5 * 3 * 10.0)
    assert row["budget_mode"] == "median"


def test_concurrency_outside_one_to_ten_is_refused(writer: TraceWriter) -> None:
    with pytest.raises(TraceError):
        writer.set_concurrency(11)


def test_kill_switch_flag(writer: TraceWriter) -> None:
    writer.set_factory_enabled(False)
    row = writer.get_installation()
    assert row is not None
    assert row["factory_enabled"] == 0


# --- configuration ----------------------------------------------------------


def test_same_vendor_roster_is_refused(writer: TraceWriter) -> None:
    with pytest.raises(TraceError, match="CHECK"):
        writer.load_config_version(
            config_sha="cfg2",
            main_commit="def",
            roster_json="{}",
            risk_yaml="",
            review_policy_yaml="",
            protected_paths_json="[]",
            quality_commands_json="{}",
            vendors_json=VENDORS_SAME,
        )


def test_config_version_insert_is_idempotent(writer: TraceWriter) -> None:
    assert not writer.load_config_version(
        config_sha="cfg1",
        main_commit="abc",
        roster_json="{}",
        risk_yaml="",
        review_policy_yaml="",
        protected_paths_json="[]",
        quality_commands_json="{}",
        vendors_json=VENDORS_OK,
    )


# --- tasks ------------------------------------------------------------------


def test_task_insert_is_idempotent_on_task_id(writer: TraceWriter) -> None:
    task_id = _task(writer)
    assert not writer.insert_task(
        task_id=task_id,
        issue_number=418,
        title=None,
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="someone",
        config_sha="cfg1",
    )
    assert writer.db.scalar("SELECT COUNT(*) FROM tasks") == 1


def test_task_requires_known_config(writer: TraceWriter) -> None:
    with pytest.raises(TraceError, match="FOREIGN KEY"):
        writer.insert_task(
            task_id="acme/atlas-api#1",
            issue_number=1,
            title=None,
            branch="agent/1",
            workspace_kind=WorkspaceKind.WORKTREE,
            budget_usd=6.0,
            admitted_by="rsouza",
            config_sha="unknown",
        )


def test_blocked_status_names_its_reason(writer: TraceWriter) -> None:
    task_id = _task(writer)
    with pytest.raises(TraceError):
        writer.set_task_status(task_id, TaskStatus.BLOCKED)
    writer.set_task_status(task_id, TaskStatus.BLOCKED, blocked_reason=BlockedReason.REPAIR_CAP)
    row = writer.get_task(task_id)
    assert row is not None
    assert (row["status"], row["blocked_reason"]) == ("blocked", "repair_cap")
    needs_human = writer.db.fetchall("SELECT task_id FROM v_needs_human")
    assert [r["task_id"] for r in needs_human] == [task_id]
    writer.set_task_status(task_id, TaskStatus.RUNNING)
    row = writer.get_task(task_id)
    assert row is not None
    assert row["blocked_reason"] is None


# --- runs and phases --------------------------------------------------------


def test_run_status_defaults_to_fail(writer: TraceWriter) -> None:
    task_id = _task(writer)
    with writer.db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, task_id, attempt, factory_version, config_sha, started_at)"
            " VALUES ('raw', ?, 1, '0.1.0', 'cfg1', 'now')",
            (task_id,),
        )
    assert writer.db.scalar("SELECT status FROM runs WHERE run_id = 'raw'") == "fail"


def test_phase_costs_roll_up_to_run_and_task(writer: TraceWriter) -> None:
    task_id = _task(writer)
    run_id = writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    p1 = writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.BUILD,
        kind=PhaseKind.AGENT,
        owner="builder",
        timeout_s=7200,
        harness="fake",
    )
    writer.finish_phase(p1, PhaseStatus.PASS, tokens_in=1000, tokens_out=200, cost_usd=1.25)
    p2 = writer.start_phase(
        run_id=run_id,
        seq=2,
        name=PhaseName.CODE_GATES,
        kind=PhaseKind.CODE,
        owner="orchestrator",
        timeout_s=600,
    )
    writer.finish_phase(p2, PhaseStatus.FAIL, error="typecheck failed")
    writer.finish_run(run_id, RunStatus.FAIL)
    run = writer.get_run(run_id)
    assert run is not None
    assert (run["tokens_in"], run["tokens_out"]) == (1000, 200)
    assert run["total_cost_usd"] == pytest.approx(1.25)
    assert run["ended_at"] is not None
    task = writer.get_task(task_id)
    assert task is not None
    assert task["spent_usd"] == pytest.approx(1.25)
    phase = writer.get_phase(p2)
    assert phase is not None
    assert phase["error"] == "typecheck failed"


def test_run_attempt_is_unique_per_task(writer: TraceWriter) -> None:
    task_id = _task(writer)
    writer.start_run(task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1")
    with pytest.raises(TraceError, match="UNIQUE"):
        writer.start_run(task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1")


# --- events append-only -----------------------------------------------------


def _raw_event(writer: TraceWriter, task_id: str) -> int:
    with writer.db.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO events (task_id, kind, actor, payload_json, payload_hash, created_at,"
            " prev_hash, hash)"
            " VALUES (?, 'log', 'orchestrator', '{\"a\":1}', 'ph', ?, 'prev', 'h1')",
            (task_id, "2026-10-24T12:00:00.000+00:00"),
        )
        return int(cursor.lastrowid or 0)


def test_events_update_aborts_except_payload_null(writer: TraceWriter) -> None:
    task_id = _task(writer)
    seq = _raw_event(writer, task_id)
    with pytest.raises(TraceError, match="append-only"):
        writer._execute("UPDATE events SET kind = 'error' WHERE seq = ?", (seq,))
    with pytest.raises(TraceError, match="append-only"):
        writer._execute("UPDATE events SET payload_json = '{}' WHERE seq = ?", (seq,))
    writer._execute("UPDATE events SET payload_json = NULL WHERE seq = ?", (seq,))
    assert writer.db.scalar("SELECT payload_json FROM events WHERE seq = ?", (seq,)) is None
    assert writer.db.scalar("SELECT payload_hash FROM events WHERE seq = ?", (seq,)) == "ph"


def test_events_delete_within_a_year_aborts(writer: TraceWriter) -> None:
    task_id = _task(writer)
    seq = _raw_event(writer, task_id)
    with pytest.raises(TraceError, match="1 year"):
        writer._execute("DELETE FROM events WHERE seq = ?", (seq,))


# --- gate results, envelopes, processes ------------------------------------


def test_gate_result_output_tail_is_capped(writer: TraceWriter) -> None:
    task_id = _task(writer)
    run_id = writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.CODE_GATES,
        kind=PhaseKind.CODE,
        owner="orchestrator",
        timeout_s=600,
    )
    long_output = "x" * 5000 + "END"
    gate_id = writer.write_gate_result(
        run_id=run_id,
        phase_id=phase_id,
        attempt=1,
        gate=GateName.TYPECHECK,
        passed=False,
        checks=[{"item": "uv run mypy src/", "ok": False, "note": "1 error"}],
        command="uv run mypy src/",
        exit_code=1,
        output_tail=long_output,
    )
    row = writer.db.fetchone("SELECT * FROM gate_results WHERE id = ?", (gate_id,))
    assert row is not None
    assert len(row["output_tail"]) == 4000
    assert row["output_tail"].endswith("END")
    assert row["passed"] == 0
    dist = writer.db.fetchall("SELECT gate, fails FROM v_gate_fail_distribution")
    assert [(r["gate"], r["fails"]) for r in dist] == [("typecheck", 1)]


def test_envelope_and_process_rows(writer: TraceWriter) -> None:
    task_id = _task(writer)
    run_id = writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.BUILD,
        kind=PhaseKind.AGENT,
        owner="builder",
        timeout_s=7200,
    )
    env_id = writer.write_envelope(
        run_id=run_id,
        phase_id=phase_id,
        agent=AgentRole.BUILDER,
        output_type=OutputType.BUILD,
        payload_json=None,
        valid=False,
        attempt=1,
        violations=[{"path": "$.changed_files", "problem": "missing"}],
    )
    env = writer.db.fetchone("SELECT * FROM envelopes WHERE envelope_id = ?", (env_id,))
    assert env is not None
    assert env["valid"] == 0
    assert "changed_files" in env["violations_json"]

    proc_id = writer.start_process(
        run_id=run_id, phase_id=phase_id, kind=ProcessKind.AGENT, name="claude", pid=4242
    )
    before = writer.get_process(proc_id)
    assert before is not None
    writer.touch_process(proc_id)
    writer.end_process(proc_id, exit_code=0)
    after = writer.get_process(proc_id)
    assert after is not None
    assert after["last_event_at"] >= before["last_event_at"]
    assert (after["exit_code"], after["ended_at"] is not None) == (0, True)
