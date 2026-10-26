"""Retention, purge and pseudonymised logins (PRD §Non-functional · Privacy; TRD §4.4).

The nightly job runs once a day from 03:00 installation time: payloads older
than ``installation.retention_days`` are nulled (``events.payload_json``,
``envelopes.payload_json``, ``gate_results.output_tail``) and one ``purge``
event names the seq range; metadata older than the year is deleted, events
first, then phases and runs nobody references. ``purge_task`` nulls one
task's payloads on request and ``purge_login`` records a login's SHA-256 so
readers show ``user:<prefix>`` from then on. A hash-covered column is never
rewritten: the chain verifies unchanged after every purge.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from orchestrator import constants
from orchestrator.models.enums import EventKind
from orchestrator.trace.db import TraceDB
from orchestrator.trace.writer import TraceWriter

RETENTION_HOUR = 3  # 03:00 installation time (TRD §4.4)
METADATA_MARGIN_DAYS = 1  # one day past the year, clear of the delete trigger's own boundary
PSEUDONYM_PREFIX = "user:"
PSEUDONYM_LEN = 12
Clock = Callable[[], datetime]
PAYLOAD_TABLES = ("events", "envelopes", "gate_results")


def utc_clock() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


# --- logins ------------------------------------------------------------------------------------


def login_hash(login: str) -> str:
    return hashlib.sha256(login.encode("utf-8")).hexdigest()


def pseudonym(login: str) -> str:
    return f"{PSEUDONYM_PREFIX}{login_hash(login)[:PSEUDONYM_LEN]}"


def pseudonymise(value: str, purged: frozenset[str]) -> str:
    """``user:<prefix>`` when ``value`` is a purged login (by hash), else the value itself."""
    return pseudonym(value) if purged and login_hash(value) in purged else value


def mask_payload(value: object, purged: frozenset[str]) -> object:
    """The same mask inside a payload: every string equal to a purged login, at any depth."""
    if isinstance(value, str):
        return pseudonymise(value, purged)
    if isinstance(value, list):
        return [mask_payload(v, purged) for v in value]
    if isinstance(value, dict):
        return {k: mask_payload(v, purged) for k, v in value.items()}
    return value


def purged_logins(writer: TraceWriter) -> frozenset[str]:
    """The SHA-256 of every login a ``purge`` event asked to pseudonymise."""
    rows = writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'purge' AND payload_json LIKE ?",
        ('%"purge":"login"%',),
    )
    hashes: set[str] = set()
    for row in rows:
        payload = json.loads(row["payload_json"])
        if isinstance(payload, dict) and isinstance(payload.get("login_sha256"), str):
            hashes.add(payload["login_sha256"])
    return frozenset(hashes)


def purge_login(writer: TraceWriter, login: str, *, actor: str) -> int:
    """Record the request; readers mask the login from now on; the event holds only a hash."""
    return writer.write_event(
        EventKind.PURGE, actor, payload={"purge": "login", "login_sha256": login_hash(login)}
    )


# --- payload nulling and metadata deletion ----------------------------------------------------


@dataclass(frozen=True)
class PurgeReport:
    purge: str  # retention | task
    events: int = 0
    envelopes: int = 0
    gate_results: int = 0
    from_seq: int | None = None
    to_seq: int | None = None
    deleted_events: int = 0
    deleted_phases: int = 0
    deleted_runs: int = 0
    event_seq: int | None = None

    def payload(self) -> dict[str, object]:
        body: dict[str, object] = {
            "purge": self.purge,
            "events": self.events,
            "envelopes": self.envelopes,
            "gate_results": self.gate_results,
            "from_seq": self.from_seq,
            "to_seq": self.to_seq,
        }
        if self.purge == "retention":
            body["deleted"] = {
                "events": self.deleted_events,
                "phases": self.deleted_phases,
                "runs": self.deleted_runs,
            }
        return body


def null_before(db: TraceDB, cutoff: str) -> PurgeReport:
    """Null every payload created before ``cutoff``; metadata columns stay."""
    with db.transaction() as conn:
        bounds = conn.execute(
            "SELECT MIN(seq), MAX(seq) FROM events WHERE created_at < ?"
            " AND payload_json IS NOT NULL",
            (cutoff,),
        ).fetchone()
        events = conn.execute(
            "UPDATE events SET payload_json = NULL WHERE created_at < ?"
            " AND payload_json IS NOT NULL",
            (cutoff,),
        ).rowcount
        envelopes = conn.execute(
            "UPDATE envelopes SET payload_json = NULL"
            " WHERE created_at < ? AND payload_json IS NOT NULL",
            (cutoff,),
        ).rowcount
        gates = conn.execute(
            "UPDATE gate_results SET output_tail = NULL"
            " WHERE created_at < ? AND output_tail IS NOT NULL",
            (cutoff,),
        ).rowcount
    return PurgeReport("retention", int(events), int(envelopes), int(gates), bounds[0], bounds[1])


def null_task(db: TraceDB, task_id: str) -> PurgeReport:
    """Null every payload of one task: its events, and the envelopes and gate output of its runs."""
    runs = "SELECT run_id FROM runs WHERE task_id = ?"
    with db.transaction() as conn:
        bounds = conn.execute(
            "SELECT MIN(seq), MAX(seq) FROM events WHERE task_id = ? AND payload_json IS NOT NULL",
            (task_id,),
        ).fetchone()
        events = conn.execute(
            "UPDATE events SET payload_json = NULL WHERE task_id = ? AND payload_json IS NOT NULL",
            (task_id,),
        ).rowcount
        envelopes = conn.execute(
            f"UPDATE envelopes SET payload_json = NULL WHERE run_id IN ({runs})"
            " AND payload_json IS NOT NULL",
            (task_id,),
        ).rowcount
        gates = conn.execute(
            f"UPDATE gate_results SET output_tail = NULL WHERE run_id IN ({runs})"
            " AND output_tail IS NOT NULL",
            (task_id,),
        ).rowcount
    return PurgeReport("task", int(events), int(envelopes), int(gates), bounds[0], bounds[1])


def delete_metadata(db: TraceDB, cutoff: str) -> tuple[int, int, int]:
    """Delete events, then phases and runs, older than ``cutoff`` that nothing still references."""
    old_phases = (
        "SELECT phase_id FROM phases p WHERE p.started_at < ?"
        " AND NOT EXISTS (SELECT 1 FROM events e WHERE e.phase_id = p.phase_id)"
        " AND NOT EXISTS (SELECT 1 FROM approvals a WHERE a.phase_id = p.phase_id)"
    )
    old_runs = (
        "SELECT run_id FROM runs r WHERE r.started_at < ?"
        " AND NOT EXISTS (SELECT 1 FROM phases p WHERE p.run_id = r.run_id)"
        " AND NOT EXISTS (SELECT 1 FROM events e WHERE e.run_id = r.run_id)"
    )
    with db.transaction() as conn:
        events = conn.execute(
            "DELETE FROM events WHERE created_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM events c WHERE c.parent_seq = events.seq)"
            " AND NOT EXISTS (SELECT 1 FROM audit_decisions a WHERE a.event_seq = events.seq)",
            (cutoff,),
        ).rowcount
        for table in ("processes", "gate_results", "envelopes"):
            conn.execute(f"DELETE FROM {table} WHERE phase_id IN ({old_phases})", (cutoff,))
        phases = conn.execute(f"DELETE FROM phases WHERE phase_id IN ({old_phases})", (cutoff,))
        for table in ("processes", "gate_results", "envelopes"):
            conn.execute(f"DELETE FROM {table} WHERE run_id IN ({old_runs})", (cutoff,))
        runs = conn.execute(f"DELETE FROM runs WHERE run_id IN ({old_runs})", (cutoff,))
    return int(events), int(phases.rowcount), int(runs.rowcount)


def purge_task(writer: TraceWriter, task_id: str, *, actor: str) -> PurgeReport | None:
    """Null one task's payloads on request and record the ``purge`` event; metadata stays."""
    if writer.get_task(task_id) is None:
        return None
    report = null_task(writer.db, task_id)
    seq = writer.write_event(
        EventKind.PURGE, actor, task_id=task_id, payload={**report.payload(), "task_id": task_id}
    )
    return replace(report, event_seq=seq)


def last_retention_day(writer: TraceWriter) -> str | None:
    row = writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'purge' AND payload_json LIKE ?"
        " ORDER BY seq DESC LIMIT 1",
        ('%"purge":"retention"%',),
    )
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    day = payload.get("day") if isinstance(payload, dict) else None
    return str(day) if day is not None else None


class RetentionJob:
    """The nightly job: once a day, from 03:00 installation time."""

    def __init__(self, writer: TraceWriter, *, clock: Clock = utc_clock) -> None:
        self.writer = writer
        self.clock = clock

    def due_day(self, now: datetime | None = None) -> str | None:
        installation = self.writer.get_installation()
        if installation is None:
            return None
        local = (now or self.clock()).astimezone(ZoneInfo(str(installation["timezone"])))
        if local.hour < RETENTION_HOUR:
            return None
        day = local.date().isoformat()
        return None if last_retention_day(self.writer) == day else day

    def run_once(self, *, now: datetime | None = None, force: bool = False) -> PurgeReport | None:
        moment = now or self.clock()
        installation = self.writer.get_installation()
        if installation is None:
            return None
        if force:
            day = moment.astimezone(ZoneInfo(str(installation["timezone"]))).date().isoformat()
        else:
            due = self.due_day(moment)
            if due is None:
                return None
            day = due
        cutoff = _iso(moment - timedelta(days=int(installation["retention_days"])))
        year = _iso(
            moment - timedelta(days=constants.METADATA_RETENTION_DAYS + METADATA_MARGIN_DAYS)
        )
        nulled = null_before(self.writer.db, cutoff)
        deleted_events, deleted_phases, deleted_runs = delete_metadata(self.writer.db, year)
        report = replace(
            nulled,
            deleted_events=deleted_events,
            deleted_phases=deleted_phases,
            deleted_runs=deleted_runs,
        )
        seq = self.writer.write_event(
            EventKind.PURGE,
            "orchestrator",
            payload={
                **report.payload(),
                "day": day,
                "older_than": cutoff,
                "metadata_older_than": year,
            },
        )
        return replace(report, event_seq=seq)

    async def run(self, stop: asyncio.Event, *, interval_s: float = 300.0) -> None:
        while not stop.is_set():
            self.run_once()
            try:
                await asyncio.wait_for(stop.wait(), interval_s)
            except TimeoutError:
                continue


__all__ = [
    "PSEUDONYM_PREFIX",
    "RETENTION_HOUR",
    "PurgeReport",
    "RetentionJob",
    "delete_metadata",
    "last_retention_day",
    "login_hash",
    "mask_payload",
    "null_before",
    "null_task",
    "pseudonym",
    "pseudonymise",
    "purge_login",
    "purge_task",
    "purged_logins",
]
