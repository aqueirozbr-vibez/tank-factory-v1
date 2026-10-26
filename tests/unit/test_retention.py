"""Unit 51: retention and purge keep the chain valid; the export signs and verifies."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from cli.main import main
from orchestrator.models.enums import (
    AgentRole,
    ApprovalGate,
    EventKind,
    GateName,
    OutputType,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    RunStatus,
    WorkspaceKind,
)
from orchestrator.trace import TraceDB, TraceWriter, verify_chain
from orchestrator.trace.canonical import canonical_json
from orchestrator.trace.chain import EventRecord, genesis_hash, payload_hash
from orchestrator.trace.export import build_export, public_key_b64, verify_export
from orchestrator.trace.retention import (
    RetentionJob,
    last_retention_day,
    login_hash,
    pseudonym,
    pseudonymise,
    purge_login,
    purge_task,
    purged_logins,
)

DAY = timedelta(days=1)
ZONE = ZoneInfo("America/Sao_Paulo")  # the fixture installation's time zone


def admit(writer: TraceWriter, issue: int = 418, *, by: str = "rsouza") -> str:
    task_id = f"acme/atlas-api#{issue}"
    writer.insert_task(
        task_id=task_id,
        issue_number=issue,
        title="Invoice due date",
        branch=f"agent/{issue}",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by=by,
        config_sha="cfg1",
    )
    return task_id


def run_and_phase(
    writer: TraceWriter, task_id: str, *, attempt: int = 1, started_at: str | None = None
) -> tuple[str, str]:
    run_id = writer.start_run(
        task_id=task_id, attempt=attempt, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = writer.start_phase(
        run_id=run_id, seq=1, name=PhaseName.BUILD, kind=PhaseKind.AGENT, owner="b", timeout_s=60
    )
    writer.finish_phase(phase_id, PhaseStatus.PASS)
    writer.finish_run(run_id, RunStatus.SUCCESS)
    if started_at is not None:
        with writer.db.transaction() as conn:
            conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", (started_at, run_id))
            conn.execute(
                "UPDATE phases SET started_at = ? WHERE phase_id = ?", (started_at, phase_id)
            )
    return run_id, phase_id


def attach_payloads(
    writer: TraceWriter, run_id: str, phase_id: str, *, created_at: str | None = None
) -> None:
    writer.write_envelope(
        run_id=run_id,
        phase_id=phase_id,
        agent=AgentRole.BUILDER,
        output_type=OutputType.BUILD,
        payload_json='{"claims":[]}',
        valid=True,
        attempt=1,
    )
    writer.write_gate_result(
        run_id=run_id,
        phase_id=phase_id,
        attempt=1,
        gate=GateName("lint"),
        passed=True,
        checks=[],
        output_tail="ok",
    )
    if created_at is not None:
        with writer.db.transaction() as conn:
            conn.execute(
                "UPDATE envelopes SET created_at = ? WHERE run_id = ?", (created_at, run_id)
            )
            conn.execute(
                "UPDATE gate_results SET created_at = ? WHERE run_id = ?", (created_at, run_id)
            )


def aged_event(db: TraceDB, *, created_at: str, payload: object, task_id: str | None = None) -> int:
    """Append a chained ``log`` event with a chosen ``created_at`` (the trigger forbids updates)."""
    with db.transaction() as conn:
        head = conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if head is None:
            row = conn.execute("SELECT org, repo, install_id FROM installation").fetchone()
            prev, seq = genesis_hash(row["org"], row["repo"], row["install_id"]), 1
        else:
            prev, seq = str(head["hash"]), int(head["seq"]) + 1
        record = EventRecord(
            seq=seq,
            kind="log",
            actor="orchestrator",
            task_id=task_id,
            run_id=None,
            phase_id=None,
            parent_seq=None,
            payload_hash=payload_hash(payload),
            tokens=None,
            started_at=None,
            ended_at=None,
            created_at=created_at,
            prev_hash=prev,
        )
        conn.execute(
            "INSERT INTO events (seq, task_id, kind, actor, payload_json, payload_hash,"
            " created_at, prev_hash, hash) VALUES (?, ?, 'log', 'orchestrator', ?, ?, ?, ?, ?)",
            (
                seq,
                task_id,
                canonical_json(payload),
                record.payload_hash,
                created_at,
                prev,
                record.record_hash(),
            ),
        )
    return seq


def local_clock(hour: int, minute: int = 0, *, days: int = 0) -> datetime:
    """Today in Sao Paulo at ``hour:minute`` (plus ``days``), as an aware UTC datetime."""
    local = datetime.now(UTC).astimezone(ZONE).replace(hour=hour, minute=minute, second=0)
    return (local + days * DAY).astimezone(UTC)


def iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - days * DAY).isoformat(timespec="milliseconds")


def key_pair(tmp_path: Path) -> tuple[Path, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    key_file = tmp_path / "export.key"
    key_file.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return key_file, public_key_b64(private)


def test_retention_runs_once_a_day_from_three_and_keeps_the_chain(
    trace_writer: TraceWriter,
) -> None:
    w, db = trace_writer, trace_writer.db
    ancient, old = iso_days_ago(400), iso_days_ago(100)
    s_ancient = aged_event(db, created_at=ancient, payload={"age": "ancient"})
    s_old = aged_event(db, created_at=old, payload={"age": "old"})
    task = admit(w)
    fresh = w.write_event(EventKind.LOG, "orchestrator", task_id=task, payload={"age": "fresh"})
    live_run, live_phase = run_and_phase(w, task)
    attach_payloads(w, live_run, live_phase, created_at=old)  # aged payload rows on a live run
    old_run, old_phase = run_and_phase(w, task, attempt=2, started_at=ancient)  # a year gone
    attach_payloads(w, old_run, old_phase, created_at=ancient)
    kept_run, kept_phase = run_and_phase(w, task, attempt=3, started_at=ancient)
    w.record_approval(  # an approval keeps its phase, and so its run
        task_id=task,
        gate=ApprovalGate.PLAN,
        actor_login="rsouza",
        bound_sha="a" * 40,
        github_event_id="d1",
        phase_id=kept_phase,
    )

    job = RetentionJob(w)
    assert job.run_once(now=local_clock(2, 30)) is None  # before 03:00 installation time
    report = job.run_once(now=local_clock(3, 5))
    assert report is not None and report.event_seq is not None
    assert (report.events, report.envelopes, report.gate_results) == (2, 2, 2)
    assert (report.from_seq, report.to_seq) == (s_ancient, s_old)
    assert (report.deleted_events, report.deleted_phases, report.deleted_runs) == (1, 1, 1)
    assert db.scalar("SELECT payload_json FROM events WHERE seq = ?", (s_old,)) is None
    assert db.scalar("SELECT COUNT(*) FROM events WHERE seq = ?", (s_ancient,)) == 0
    assert db.scalar("SELECT payload_json FROM events WHERE seq = ?", (fresh,)) is not None
    assert db.scalar("SELECT payload_json FROM envelopes WHERE run_id = ?", (live_run,)) is None
    assert db.scalar("SELECT output_tail FROM gate_results WHERE run_id = ?", (live_run,)) is None
    assert db.scalar("SELECT COUNT(*) FROM runs WHERE run_id = ?", (old_run,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM runs WHERE run_id = ?", (kept_run,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM tasks") == 1  # tasks are never deleted
    assert verify_chain(db).ok

    day = local_clock(3, 5).astimezone(ZONE).date().isoformat()
    assert last_retention_day(w) == day
    assert job.run_once(now=local_clock(23, 0)) is None  # once a day
    assert job.run_once(now=local_clock(3, 5, days=1)) is not None
    purges = [
        json.loads(r["payload_json"])
        for r in db.fetchall("SELECT payload_json FROM events WHERE kind = 'purge' ORDER BY seq")
    ]
    assert [p["day"] for p in purges] == [
        day,
        (local_clock(3, 5, days=1)).astimezone(ZONE).date().isoformat(),
    ]
    assert purges[0]["deleted"] == {"events": 1, "phases": 1, "runs": 1}
    assert purges[0]["older_than"] < purges[0]["day"] and verify_chain(db).ok


async def test_retention_loop_ticks_once_per_day(trace_writer: TraceWriter) -> None:
    job = RetentionJob(trace_writer, clock=lambda: local_clock(4, 0))
    stop = asyncio.Event()
    loop = asyncio.create_task(job.run(stop, interval_s=0.01))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(loop, 2)
    assert trace_writer.db.scalar("SELECT COUNT(*) FROM events WHERE kind = 'purge'") == 1


def test_purge_task_nulls_its_payloads_and_the_chain_still_verifies(
    trace_writer: TraceWriter,
) -> None:
    w = trace_writer
    task, other = admit(w), admit(w, 419)
    run_id, phase_id = run_and_phase(w, task)
    attach_payloads(w, run_id, phase_id)
    mine = w.write_event(
        EventKind.TOOL_CALL,
        "builder",
        task_id=task,
        run_id=run_id,
        phase_id=phase_id,
        payload={"tool": "Bash", "args": "cat secrets"},
    )
    theirs = w.write_event(EventKind.LOG, "orchestrator", task_id=other, payload={"keep": True})

    report = purge_task(w, task, actor="mlima")
    assert report is not None and report.events == 1 and report.envelopes == 1
    assert report.gate_results == 1 and (report.from_seq, report.to_seq) == (mine, mine)
    assert w.db.scalar("SELECT payload_json FROM events WHERE seq = ?", (mine,)) is None
    assert w.db.scalar("SELECT payload_json FROM events WHERE seq = ?", (theirs,)) is not None
    assert w.db.scalar("SELECT payload_json FROM envelopes WHERE run_id = ?", (run_id,)) is None
    assert w.db.scalar("SELECT output_tail FROM gate_results WHERE run_id = ?", (run_id,)) is None
    purge = w.db.fetchone(
        "SELECT actor, payload_json FROM events WHERE seq = ?", (report.event_seq,)
    )
    assert purge is not None and purge["actor"] == "mlima"
    assert json.loads(purge["payload_json"])["task_id"] == task
    assert verify_chain(w.db).ok
    assert purge_task(w, "acme/atlas-api#999", actor="mlima") is None


def test_purge_login_masks_the_login_in_the_listing_and_the_export(
    trace_writer: TraceWriter, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    w = trace_writer
    task = admit(w, by="rsouza")
    w.write_event(
        EventKind.APPROVAL,
        "rsouza",
        task_id=task,
        payload={"actor_login": "rsouza", "bound_sha": "a" * 40, "note": ["rsouza"]},
    )
    seq = purge_login(w, "rsouza", actor="mlima")
    row = w.db.fetchone("SELECT payload_json FROM events WHERE seq = ?", (seq,))
    assert row is not None and "rsouza" not in row["payload_json"]
    assert login_hash("rsouza") in row["payload_json"]
    purged = purged_logins(w)
    assert (
        pseudonymise("rsouza", purged) == pseudonym("rsouza") == "user:" + login_hash("rsouza")[:12]
    )
    assert (
        pseudonymise("mlima", purged) == "mlima" and pseudonymise("rsouza", frozenset()) == "rsouza"
    )

    private = ed25519.Ed25519PrivateKey.generate()
    result = build_export(w.db, private, task_id=task, purged=purged)
    text = result.text()
    assert (
        "rsouza" not in text and text.count(pseudonym("rsouza")) >= 3
    )  # actor and two payload strings
    assert verify_export(text, public_key_b64(private)).ok  # masked lines keep their stored hash

    assert main(["trace", "--task", task, "--data-dir", str(tmp_path)]) == 0
    listing = capsys.readouterr().out
    assert "rsouza" not in listing and pseudonym("rsouza") in listing


def test_export_verifies_with_the_install_public_key_and_catches_tampering(
    trace_writer: TraceWriter,
) -> None:
    w = trace_writer
    task = admit(w)
    seqs = [
        w.write_event(EventKind.LOG, "orchestrator", task_id=task, payload={"n": n})
        for n in range(3)
    ]
    private = ed25519.Ed25519PrivateKey.generate()
    pub = public_key_b64(private)
    w.set_export_pubkey(pub)
    installation = w.get_installation()
    assert (
        installation is not None and installation["export_pubkey"] == pub
    )  # the install log's key

    result = build_export(w.db, private, task_id=task)
    assert result.records == 3 and (result.from_seq, result.to_seq) == (seqs[0], seqs[-1])
    check = verify_export(result.text(), pub)
    assert check.ok and check.records == 3 and check.head_hash == result.head_hash
    lines = result.text().splitlines()
    trailer = json.loads(lines[-1])
    assert set(trailer) == {
        "headHash",
        "fromSeq",
        "toSeq",
        "exportedAt",
        "factoryVersion",
        "signature",
    }

    def with_line(index: int, line: dict[str, object]) -> str:
        edited = [*lines]
        edited[index] = canonical_json(line)
        return "\n".join(edited) + "\n"

    payload_changed = json.loads(lines[1])
    payload_changed["payload"] = {"n": 99}
    assert "payload hash" in (verify_export(with_line(1, payload_changed), pub).reason or "")
    actor_changed = json.loads(lines[1])
    actor_changed["actor"] = "mallory"
    assert "record hash" in (verify_export(with_line(1, actor_changed), pub).reason or "")
    trailer_changed = dict(trailer)
    trailer_changed["toSeq"] = 1
    assert "signature" in (verify_export(with_line(-1, trailer_changed), pub).reason or "")
    foreign = public_key_b64(ed25519.Ed25519PrivateKey.generate())
    assert not verify_export(result.text(), foreign).ok
    assert not verify_export("", pub).ok

    whole = build_export(w.db, private, from_seq=1)  # a contiguous range checks every link
    assert verify_export(whole.text(), pub).ok and whole.records >= 3
    whole_lines = whole.text().splitlines()
    broken = json.loads(whole_lines[1])
    broken["prev_hash"] = "0" * 64
    broken_text = "\n".join([whole_lines[0], canonical_json(broken), *whole_lines[2:]]) + "\n"
    assert "record hash" in (verify_export(broken_text, pub).reason or "")


def test_cli_purge_and_export_round_trip(
    trace_writer: TraceWriter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FACTORY_EXPORT_KEY_FILE", raising=False)
    w = trace_writer
    task = admit(w)
    w.write_event(EventKind.LOG, "builder", task_id=task, payload={"tool": "Bash"})
    key_file, pub = key_pair(tmp_path)
    w.set_export_pubkey(pub)
    out = tmp_path / "export.jsonl"
    data = ["--data-dir", str(tmp_path)]
    exported = [
        "export",
        "--task",
        task,
        "--key",
        str(key_file),
        "--out",
        str(out),
        "--by",
        "mlima",
    ]
    assert main([*exported, *data]) == 0
    assert "exported 1 records" in capsys.readouterr().out
    assert verify_export(out.read_text(encoding="utf-8"), pub).ok
    assert main(["export", "--task", "acme/atlas-api#999", "--key", str(key_file), *data]) == 1
    assert main(["export", "--task", task, *data]) == 2  # no key configured
    assert main(["purge", *data]) == 2
    assert main(["purge", "--task", task, "--by", "mlima", *data]) == 0
    assert "purged" in capsys.readouterr().out
    assert main(["purge", "--login", "rsouza", "--by", "mlima", *data]) == 0
    assert verify_chain(w.db).ok
    assert main([*exported, *data]) == 0
    text = out.read_text(encoding="utf-8")
    assert '"payload":null' in text and verify_export(text, pub).ok
    assert w.db.scalar("SELECT COUNT(*) FROM events WHERE kind = 'export'") == 2
