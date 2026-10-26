"""``factory purge`` and ``factory export`` (TRD §4.3, §4.4)."""

import getpass
from pathlib import Path

from cli.common import open_trace, say, settings_for
from orchestrator.errors import FactoryError
from orchestrator.models.enums import EventKind
from orchestrator.trace.export import build_export, load_private_key
from orchestrator.trace.retention import purge_login, purge_task, purged_logins


def _actor(by: str | None) -> str:
    return by or getpass.getuser()


def purge(
    *,
    task_id: str | None,
    login: str | None,
    by: str | None = None,
    data_dir: Path | None = None,
) -> int:
    """Null one task's payloads, or pseudonymise one login, and record the ``purge`` event."""
    if (task_id is None) == (login is None):
        say("purge takes exactly one of --task or --login")
        return 2
    db, writer = open_trace(settings_for(data_dir))
    try:
        actor = _actor(by)
        if task_id is not None:
            report = purge_task(writer, task_id, actor=actor)
            if report is None:
                say(f"no task {task_id}")
                return 1
            say(
                f"purged {task_id}: {report.events} event payloads, {report.envelopes} envelopes,"
                f" {report.gate_results} gate outputs (seq {report.from_seq}..{report.to_seq})"
                f" · purge event {report.event_seq} by {actor}"
            )
            return 0
        assert login is not None
        seq = purge_login(writer, login, actor=actor)
        say(f"login pseudonymised from now on · purge event {seq} by {actor}")
        return 0
    finally:
        db.close()


def export(
    *,
    task_id: str | None = None,
    from_seq: int | None = None,
    to_seq: int | None = None,
    out: Path | None = None,
    key: Path | None = None,
    by: str | None = None,
    data_dir: Path | None = None,
) -> int:
    """Signed JSONL: one task, or a seq range of the whole installation."""
    settings = settings_for(data_dir)
    key_file = key or settings.export_key_file
    if key_file is None or not key_file.is_file():
        say("no export key: pass --key or set FACTORY_EXPORT_KEY_FILE (factory install writes it)")
        return 2
    db, writer = open_trace(settings)
    try:
        if task_id is not None and writer.get_task(task_id) is None:
            say(f"no task {task_id}")
            return 1
        try:
            private = load_private_key(key_file)
        except (FactoryError, ValueError) as exc:
            say(f"export key unusable: {exc}")
            return 2
        result = build_export(
            db,
            private,
            task_id=task_id,
            from_seq=from_seq,
            to_seq=to_seq,
            purged=purged_logins(writer),
        )
        actor = _actor(by)
        seq = writer.write_event(
            EventKind.EXPORT,
            actor,
            task_id=task_id,
            payload={
                "export": "task" if task_id is not None else "range",
                "task_id": task_id,
                "from_seq": result.from_seq,
                "to_seq": result.to_seq,
                "records": result.records,
                "head_hash": result.head_hash,
            },
        )
        text = result.text()
        if out is None:
            print(text, end="")
        else:
            out.write_text(text, encoding="utf-8")
            say(
                f"exported {result.records} records to {out}"
                f" (seq {result.from_seq}..{result.to_seq}, head {(result.head_hash or '')[:12]})"
                f" · export event {seq} by {actor}"
            )
        return 0
    finally:
        db.close()


__all__ = ["export", "purge"]
