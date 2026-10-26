"""Unit 03: the events hash chain."""

import hashlib
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from orchestrator.errors import TraceError
from orchestrator.models.enums import EventKind
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.trace.chain import (
    EventRecord,
    append_event,
    genesis_hash,
    null_payloads,
    payload_hash,
    verify_chain,
)


def _drop_append_only_triggers(db: TraceDB) -> None:
    """Simulate an attacker editing the file with a tool that ignores triggers."""
    with db.transaction() as conn:
        conn.execute("DROP TRIGGER events_append_only_u")
        conn.execute("DROP TRIGGER events_append_only_d")


def test_first_event_links_to_genesis(trace_writer: TraceWriter) -> None:
    seq = trace_writer.write_event(EventKind.LOG, "orchestrator", payload={"msg": "hello"})
    assert seq == 1
    installation = trace_writer.get_installation()
    assert installation is not None
    assert len(installation["install_id"]) == 32
    row = trace_writer.db.fetchone("SELECT * FROM events WHERE seq = 1")
    assert row is not None
    assert row["prev_hash"] == genesis_hash("acme", "atlas-api", installation["install_id"])
    assert row["payload_json"] == '{"msg":"hello"}'
    assert row["payload_hash"] == hashlib.sha256(b'{"msg":"hello"}').hexdigest()
    assert EventRecord.from_row(row).record_hash() == row["hash"]


def test_null_payload_hashes_as_json_null(trace_writer: TraceWriter) -> None:
    trace_writer.write_event(EventKind.PHASE_START, "orchestrator")
    row = trace_writer.db.fetchone("SELECT payload_json, payload_hash FROM events WHERE seq = 1")
    assert row is not None
    assert row["payload_json"] is None
    assert row["payload_hash"] == payload_hash(None) == hashlib.sha256(b"null").hexdigest()


def test_chain_of_events_verifies(trace_writer: TraceWriter) -> None:
    for i in range(10):
        trace_writer.write_event(EventKind.TOOL_CALL, "builder", payload={"i": i, "ok": True})
    report = verify_chain(trace_writer.db)
    assert report.ok and report.records == 10
    assert report.head_hash == trace_writer.db.scalar("SELECT hash FROM events WHERE seq = 10")
    partial = verify_chain(trace_writer.db, 3, 5)
    assert partial.ok and partial.records == 3


def test_event_before_installation_is_refused(trace_db: TraceDB) -> None:
    with pytest.raises(TraceError, match="installation row must exist"):
        append_event(trace_db, kind=EventKind.LOG, actor="orchestrator")


def test_nulling_payloads_keeps_the_chain_valid(trace_writer: TraceWriter) -> None:
    for i in range(6):
        trace_writer.write_event(EventKind.TOOL_CALL, "builder", payload={"args": "x" * i})
    before = trace_writer.db.fetchall("SELECT payload_hash FROM events ORDER BY seq")
    assert null_payloads(trace_writer.db, 1, 3) == 3
    assert null_payloads(trace_writer.db, 1, 3) == 0
    after = trace_writer.db.fetchall("SELECT payload_json, payload_hash FROM events ORDER BY seq")
    assert [r["payload_json"] is None for r in after] == [True, True, True, False, False, False]
    assert [r["payload_hash"] for r in after] == [r["payload_hash"] for r in before]
    report = verify_chain(trace_writer.db)
    assert report.ok and report.records == 6


def test_tampered_actor_is_detected(trace_writer: TraceWriter) -> None:
    for _ in range(4):
        trace_writer.write_event(EventKind.CONTROL, "mlima", payload={"action": "kill"})
    _drop_append_only_triggers(trace_writer.db)
    with trace_writer.db.transaction() as conn:
        conn.execute("UPDATE events SET actor = 'intruder' WHERE seq = 3")
    report = verify_chain(trace_writer.db)
    assert not report.ok
    assert report.first_bad_seq == 3
    assert report.reason == "record hash does not match"


def test_tampered_payload_is_detected(trace_writer: TraceWriter) -> None:
    for _ in range(3):
        trace_writer.write_event(EventKind.AUDIT, "mlima", payload={"decision": "agree"})
    _drop_append_only_triggers(trace_writer.db)
    with trace_writer.db.transaction() as conn:
        conn.execute('UPDATE events SET payload_json = \'{"decision":"disagree"}\' WHERE seq = 2')
    report = verify_chain(trace_writer.db)
    assert not report.ok
    assert (report.first_bad_seq, report.reason) == (2, "payload hash does not match")


def test_broken_link_is_detected(trace_writer: TraceWriter) -> None:
    for _ in range(3):
        trace_writer.write_event(EventKind.LOG, "orchestrator")
    _drop_append_only_triggers(trace_writer.db)
    with trace_writer.db.transaction() as conn:
        conn.execute("UPDATE events SET prev_hash = 'forged' WHERE seq = 2")
    report = verify_chain(trace_writer.db)
    assert (report.first_bad_seq, report.reason) == (2, "prev_hash does not match predecessor")


def test_empty_chain_verifies(trace_writer: TraceWriter) -> None:
    report = verify_chain(trace_writer.db)
    assert report == verify_chain(trace_writer.db, 1, 10)
    assert report.ok and report.records == 0 and report.head_hash is None


def test_event_rows_carry_context(trace_writer: TraceWriter) -> None:
    from orchestrator.models.enums import PhaseKind, PhaseName, WorkspaceKind

    task_id = "acme/atlas-api#7"
    trace_writer.insert_task(
        task_id=task_id,
        issue_number=7,
        title=None,
        branch="agent/7",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    run_id = trace_writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = trace_writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.BUILD,
        kind=PhaseKind.AGENT,
        owner="builder",
        timeout_s=10,
    )
    parent = trace_writer.write_event(
        EventKind.PHASE_START, "orchestrator", task_id=task_id, run_id=run_id, phase_id=phase_id
    )
    child = trace_writer.write_event(
        EventKind.TOOL_CALL,
        "builder",
        task_id=task_id,
        run_id=run_id,
        phase_id=phase_id,
        parent_seq=parent,
        payload={"tool": "Read", "ok": True},
        tokens=12,
        started_at="2026-10-24T14:21:51.000+00:00",
        ended_at="2026-10-24T14:21:51.012+00:00",
    )
    row = trace_writer.db.fetchone("SELECT * FROM events WHERE seq = ?", (child,))
    assert row is not None
    assert (row["parent_seq"], row["tokens"], row["phase_id"]) == (parent, 12, phase_id)
    with pytest.raises(TraceError, match="FOREIGN KEY"):
        trace_writer.write_event(EventKind.LOG, "orchestrator", phase_id="missing")
    assert verify_chain(trace_writer.db).ok


event_specs = st.lists(
    st.tuples(
        st.sampled_from(list(EventKind)),
        st.text(min_size=1, max_size=12),
        st.none()
        | st.dictionaries(
            st.text(max_size=6),
            st.integers(min_value=-(2**53), max_value=2**53) | st.text(),
            max_size=3,
        ),
    ),
    min_size=1,
    max_size=15,
)


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(event_specs)
def test_any_sequence_of_events_verifies(
    tmp_path_factory: pytest.TempPathFactory,
    specs: list[tuple[EventKind, str, dict[str, object] | None]],
) -> None:
    path: Path = tmp_path_factory.mktemp("chain") / "trace.db"
    db = TraceDB.open(path)
    try:
        writer = TraceWriter(db)
        writer.load_config_version(
            config_sha="cfg",
            main_commit="m",
            roster_json="{}",
            risk_yaml="",
            review_policy_yaml="",
            protected_paths_json="[]",
            quality_commands_json="{}",
            vendors_json='{"builder":"a","reviewer":"b"}',
        )
        writer.insert_installation(
            org="o",
            repo="r",
            factory_version="0",
            config_sha="cfg",
            per_task_budget_usd=1.0,
            export_pubkey="pk",
        )
        for kind, actor, payload in specs:
            writer.write_event(kind, actor, payload=payload)
        report = verify_chain(db)
        assert report.ok
        assert report.records == len(specs)
    finally:
        db.close()
