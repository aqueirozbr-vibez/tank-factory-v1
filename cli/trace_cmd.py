"""``factory trace`` and ``factory verify``: read the ledger from the VM shell."""

import json
from pathlib import Path

from cli.common import open_trace, say, settings_for
from orchestrator.trace import verify_chain
from orchestrator.trace.retention import mask_payload, pseudonymise, purged_logins


def _summary(payload_json: str | None, purged: frozenset[str] = frozenset()) -> str:
    if not payload_json:
        return ""
    try:
        payload = mask_payload(json.loads(payload_json), purged)
    except ValueError:
        return payload_json[:80]
    if isinstance(payload, dict):
        if "tool" in payload:
            args = json.dumps(payload.get("args"), sort_keys=True)
            return (
                f"{payload['tool']} {args[:60]}"
                f" ok={payload.get('ok')} {payload.get('duration_ms')}ms"
            )
        if "gate" in payload:
            return f"{payload['gate']} violations={payload.get('violations')}"
        keys = ", ".join(f"{k}={payload[k]}" for k in list(payload)[:4])
        return keys[:100]
    return str(payload)[:80]


def trace(*, task_id: str, phase_id: str | None = None, data_dir: Path | None = None) -> int:
    db, writer = open_trace(settings_for(data_dir))
    try:
        task = writer.get_task(task_id)
        if task is None:
            say(f"no task {task_id}")
            return 1
        if phase_id is None:
            rows = db.fetchall("SELECT * FROM events WHERE task_id = ? ORDER BY seq", (task_id,))
        else:
            rows = db.fetchall(
                "SELECT * FROM events WHERE task_id = ? AND phase_id = ? ORDER BY seq",
                (task_id, phase_id),
            )
        purged = purged_logins(writer)
        for row in rows:
            actor = pseudonymise(str(row["actor"]), purged)
            say(
                f"{row['seq']:>6}  {row['created_at'][11:23]}  {row['kind']:<14} {actor:<12}"
                f" {_summary(row['payload_json'], purged)}"
            )
        tool_calls = sum(1 for r in rows if r["kind"] == "tool_call")
        cost = db.scalar(
            "SELECT COALESCE(SUM(total_cost_usd), 0) FROM runs WHERE task_id = ?", (task_id,)
        )
        reason = task["blocked_reason"]
        status = str(task["status"]) + (f" ({reason})" if reason else "")
        say(
            f"{len(rows)} events · {tool_calls} tool calls"
            f" · cost ${float(cost or 0):.2f} · status {status}"
        )
        return 0
    finally:
        db.close()


def verify(
    *, from_seq: int | None = None, to_seq: int | None = None, data_dir: Path | None = None
) -> int:
    db, _ = open_trace(settings_for(data_dir))
    try:
        report = verify_chain(db, from_seq, to_seq)
        if report.ok:
            head = (report.head_hash or "")[:12]
            say(f"chain verified · {report.records} records · head {head}")
            return 0
        say(f"chain BROKEN at seq {report.first_bad_seq}: {report.reason}")
        return 1
    finally:
        db.close()
