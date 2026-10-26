"""The ``events`` hash chain: append, verify, and the retention primitive.

One chain per installation, ordered by ``seq`` (TRD §4.3). The covered
record excludes ``payload_json`` and includes ``payload_hash``, so retention
can null a payload and the chain still verifies.
"""

import hashlib
import sqlite3
from dataclasses import dataclass

from orchestrator.clock import utc_now
from orchestrator.errors import TraceError
from orchestrator.models.enums import EventKind
from orchestrator.trace.canonical import canonical_bytes, canonical_json, parse_canonical
from orchestrator.trace.db import TraceDB

GENESIS_PREFIX = "factory-trace-genesis:"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def genesis_hash(org: str, repo: str, install_id: str) -> str:
    return sha256_hex(f"{GENESIS_PREFIX}{org}/{repo}:{install_id}".encode())


def payload_hash(payload: object) -> str:
    return sha256_hex(canonical_bytes(payload))


@dataclass(frozen=True)
class EventRecord:
    """The fields the chain covers, in the TRD's order."""

    seq: int
    kind: str
    actor: str
    task_id: str | None
    run_id: str | None
    phase_id: str | None
    parent_seq: int | None
    payload_hash: str
    tokens: int | None
    started_at: str | None
    ended_at: str | None
    created_at: str
    prev_hash: str

    def covered(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "actor": self.actor,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "phase_id": self.phase_id,
            "parent_seq": self.parent_seq,
            "payload_hash": self.payload_hash,
            "tokens": self.tokens,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "created_at": self.created_at,
            "prev_hash": self.prev_hash,
        }

    def record_hash(self) -> str:
        return sha256_hex(canonical_bytes(self.covered()))

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "EventRecord":
        return cls(
            seq=int(row["seq"]),
            kind=str(row["kind"]),
            actor=str(row["actor"]),
            task_id=row["task_id"],
            run_id=row["run_id"],
            phase_id=row["phase_id"],
            parent_seq=row["parent_seq"],
            payload_hash=str(row["payload_hash"]),
            tokens=row["tokens"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            created_at=str(row["created_at"]),
            prev_hash=str(row["prev_hash"]),
        )


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    records: int
    head_hash: str | None
    first_bad_seq: int | None = None
    reason: str | None = None


def _genesis_for(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT org, repo, install_id FROM installation WHERE id = 1").fetchone()
    if row is None:
        raise TraceError("the installation row must exist before the first event is written")
    install_id = str(row["install_id"])
    if not install_id:
        raise TraceError("installation.install_id is empty; the genesis hash needs it")
    return genesis_hash(str(row["org"]), str(row["repo"]), install_id)


def append_event(
    db: TraceDB,
    *,
    kind: EventKind,
    actor: str,
    task_id: str | None = None,
    run_id: str | None = None,
    phase_id: str | None = None,
    parent_seq: int | None = None,
    payload: object = None,
    tokens: int | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> int:
    """Append one chained event and return its ``seq``."""
    payload_text = None if payload is None else canonical_json(payload)
    p_hash = payload_hash(payload)
    try:
        with db.transaction() as conn:
            head = conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = _genesis_for(conn) if head is None else str(head["hash"])
            seq_row = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'events'"
            ).fetchone()
            last_seq = int(seq_row["seq"]) if seq_row is not None else 0
            if head is not None:
                last_seq = max(last_seq, int(head["seq"]))
            record = EventRecord(
                seq=last_seq + 1,
                kind=str(kind),
                actor=actor,
                task_id=task_id,
                run_id=run_id,
                phase_id=phase_id,
                parent_seq=parent_seq,
                payload_hash=p_hash,
                tokens=tokens,
                started_at=started_at,
                ended_at=ended_at,
                created_at=utc_now(),
                prev_hash=prev_hash,
            )
            conn.execute(
                "INSERT INTO events (seq, run_id, phase_id, task_id, parent_seq, kind, actor,"
                " payload_json, payload_hash, tokens, started_at, ended_at, created_at,"
                " prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.seq,
                    record.run_id,
                    record.phase_id,
                    record.task_id,
                    record.parent_seq,
                    record.kind,
                    record.actor,
                    payload_text,
                    record.payload_hash,
                    record.tokens,
                    record.started_at,
                    record.ended_at,
                    record.created_at,
                    record.prev_hash,
                    record.record_hash(),
                ),
            )
            return record.seq
    except sqlite3.IntegrityError as exc:
        raise TraceError(f"event write refused: {exc}") from exc


def verify_chain(
    db: TraceDB, from_seq: int | None = None, to_seq: int | None = None
) -> ChainReport:
    """Recompute every hash and every link in ``[from_seq, to_seq]``."""
    lower = 1 if from_seq is None else from_seq
    if to_seq is None:
        rows = db.fetchall("SELECT * FROM events WHERE seq >= ? ORDER BY seq", (lower,))
    else:
        rows = db.fetchall(
            "SELECT * FROM events WHERE seq >= ? AND seq <= ? ORDER BY seq", (lower, to_seq)
        )
    if not rows:
        return ChainReport(ok=True, records=0, head_hash=None)

    first = EventRecord.from_row(rows[0])
    if first.seq == 1:
        installation = db.fetchone("SELECT org, repo, install_id FROM installation WHERE id = 1")
        if installation is None:
            return ChainReport(False, 0, None, first.seq, "installation row missing")
        expected_prev = genesis_hash(
            str(installation["org"]), str(installation["repo"]), str(installation["install_id"])
        )
    else:
        predecessor = db.fetchone("SELECT hash FROM events WHERE seq = ?", (first.seq - 1,))
        # A predecessor older than the metadata year may be gone; the stored
        # link is then taken as given and every later link is still checked.
        expected_prev = first.prev_hash if predecessor is None else str(predecessor["hash"])

    head_hash: str | None = None
    for row in rows:
        record = EventRecord.from_row(row)
        if record.prev_hash != expected_prev:
            return ChainReport(False, 0, None, record.seq, "prev_hash does not match predecessor")
        stored_hash = str(row["hash"])
        if record.record_hash() != stored_hash:
            return ChainReport(False, 0, None, record.seq, "record hash does not match")
        payload_json = row["payload_json"]
        if payload_json is not None:
            try:
                replayed = payload_hash(parse_canonical(payload_json))
            except TraceError:
                return ChainReport(False, 0, None, record.seq, "payload is not canonical JSON")
            if replayed != record.payload_hash:
                return ChainReport(False, 0, None, record.seq, "payload hash does not match")
        expected_prev = stored_hash
        head_hash = stored_hash
    return ChainReport(ok=True, records=len(rows), head_hash=head_hash)


def null_payloads(db: TraceDB, from_seq: int, to_seq: int) -> int:
    """Retention primitive: null ``payload_json`` in a range; the chain stays valid."""
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE events SET payload_json = NULL"
            " WHERE seq >= ? AND seq <= ? AND payload_json IS NOT NULL",
            (from_seq, to_seq),
        )
        return int(cursor.rowcount)
