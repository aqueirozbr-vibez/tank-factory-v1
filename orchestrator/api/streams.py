"""Server-sent events: a task's live tail and the change notices (TRD §5.2, §6.1).

Both streams poll the trace every ``POLL_S`` (a row appears within 2 s of
its write, p95) and send ``event: ping`` after ``PING_S`` of silence; a
client resumes a tail from its last ``id`` (the event ``seq``). The tail
masks purged logins like every other reader.
"""

import asyncio
import json
import sqlite3
from collections.abc import AsyncGenerator, Callable

from orchestrator.trace.retention import mask_payload, pseudonymise
from orchestrator.trace.writer import TraceWriter

POLL_S = 0.5
PING_S = 15.0
BATCH = 200


def sse_frame(event: str, data: object, *, id: int | None = None) -> str:
    lines = [f"event: {event}"]
    if id is not None:
        lines.append(f"id: {id}")
    body = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    lines.append(f"data: {body}")
    return "\n".join(lines) + "\n\n"


def event_row(row: sqlite3.Row, purged: frozenset[str]) -> dict[str, object]:
    raw = row["payload_json"]
    payload = None if raw is None else json.loads(raw)
    return {
        "seq": int(row["seq"]),
        "kind": row["kind"],
        "actor": pseudonymise(str(row["actor"]), purged),
        "task_id": row["task_id"],
        "run_id": row["run_id"],
        "phase_id": row["phase_id"],
        "parent_seq": row["parent_seq"],
        "payload": mask_payload(payload, purged) if purged else payload,
        "tokens": row["tokens"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "created_at": row["created_at"],
        "hash": row["hash"],
    }


async def task_events_stream(
    writer: TraceWriter,
    task_id: str,
    *,
    after: int,
    purged: frozenset[str],
    on_close: Callable[[], None],
    poll_s: float = POLL_S,
    ping_s: float = PING_S,
) -> AsyncGenerator[str, None]:
    """``event: row`` per insert after ``after``; ``event: ping`` through silence; resume by id."""
    last = after
    idle = 0.0
    try:
        while True:
            rows = writer.db.fetchall(
                "SELECT * FROM events WHERE task_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (task_id, last, BATCH),
            )
            if rows:
                for row in rows:
                    last = int(row["seq"])
                    yield sse_frame("row", event_row(row, purged), id=last)
                idle = 0.0
                continue
            await asyncio.sleep(poll_s)
            idle += poll_s
            if idle >= ping_s:
                yield sse_frame("ping", {"last_seq": last})
                idle = 0.0
    finally:
        on_close()


async def changes_stream(
    writer: TraceWriter,
    *,
    on_close: Callable[[], None],
    poll_s: float = POLL_S,
    ping_s: float = PING_S,
) -> AsyncGenerator[str, None]:
    """``task_changed``, ``eval_complete`` and ``config_loaded`` notices for cache invalidation."""
    db = writer.db
    last_seq = int(db.scalar("SELECT COALESCE(MAX(seq), 0) FROM events") or 0)
    last_loaded = str(db.scalar("SELECT COALESCE(MAX(loaded_at), '') FROM config_versions") or "")
    last_eval = str(
        db.scalar("SELECT COALESCE(MAX(ended_at), '') FROM eval_runs WHERE status = 'complete'")
        or ""
    )
    idle = 0.0
    try:
        while True:
            sent = False
            rows = db.fetchall(
                "SELECT seq, task_id FROM events WHERE seq > ? AND task_id IS NOT NULL"
                " ORDER BY seq LIMIT ?",
                (last_seq, BATCH),
            )
            head = db.scalar("SELECT COALESCE(MAX(seq), 0) FROM events")
            seen: dict[str, int] = {}
            for row in rows:
                seen[str(row["task_id"])] = int(row["seq"])
            for task_id, seq in seen.items():
                yield sse_frame("task_changed", {"task_id": task_id, "seq": seq}, id=seq)
                sent = True
            last_seq = max(last_seq, int(head or 0), *(seen.values() or [0]))
            for row in db.fetchall(
                "SELECT config_sha, loaded_at FROM config_versions WHERE loaded_at > ?"
                " ORDER BY loaded_at",
                (last_loaded,),
            ):
                last_loaded = str(row["loaded_at"])
                yield sse_frame("config_loaded", {"config_sha": row["config_sha"]})
                sent = True
            for row in db.fetchall(
                "SELECT id, set_id, pass_rate, blocked, ended_at FROM eval_runs"
                " WHERE status = 'complete' AND ended_at > ? ORDER BY ended_at",
                (last_eval,),
            ):
                last_eval = str(row["ended_at"])
                yield sse_frame(
                    "eval_complete",
                    {
                        "run_id": row["id"],
                        "set_id": row["set_id"],
                        "pass_rate": row["pass_rate"],
                        "blocked": None if row["blocked"] is None else bool(row["blocked"]),
                    },
                )
                sent = True
            if sent:
                idle = 0.0
                continue
            await asyncio.sleep(poll_s)
            idle += poll_s
            if idle >= ping_s:
                yield sse_frame("ping", {"last_seq": last_seq})
                idle = 0.0
    finally:
        on_close()


__all__ = [
    "BATCH",
    "PING_S",
    "POLL_S",
    "changes_stream",
    "event_row",
    "sse_frame",
    "task_events_stream",
]
