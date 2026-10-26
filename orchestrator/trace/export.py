"""Signed JSONL export of the chain (TRD §4.3; PRD §Non-functional · Auditability).

One line per event: the covered record, its stored ``hash`` and its payload
(``null`` once retention took it), then one trailer
``{headHash, fromSeq, toSeq, exportedAt, factoryVersion, signature}`` signed
with the installation's Ed25519 export key. A purged login appears as
``user:<sha256 prefix>`` in ``actor`` and inside payload strings; such a
line keeps its stored hash, which cannot be replayed without the login.
"""

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from orchestrator.clock import utc_now
from orchestrator.errors import TraceError
from orchestrator.trace.canonical import canonical_bytes, canonical_json, parse_canonical
from orchestrator.trace.chain import EventRecord, payload_hash, sha256_hex
from orchestrator.trace.db import TraceDB
from orchestrator.trace.retention import PSEUDONYM_PREFIX, mask_payload, pseudonymise
from orchestrator.version import factory_version

TRAILER_FIELDS = ("headHash", "fromSeq", "toSeq", "exportedAt", "factoryVersion")


@dataclass(frozen=True)
class Export:
    lines: tuple[str, ...]  # records, canonical JSON
    trailer: dict[str, object]  # signed
    records: int
    from_seq: int | None
    to_seq: int | None
    head_hash: str | None

    def text(self) -> str:
        return "".join(f"{line}\n" for line in (*self.lines, canonical_json(self.trailer)))


@dataclass(frozen=True)
class ExportCheck:
    ok: bool
    records: int
    head_hash: str | None
    reason: str | None = None


def load_private_key(path: Path) -> ed25519.Ed25519PrivateKey:
    loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(loaded, ed25519.Ed25519PrivateKey):
        raise TraceError(f"{path} is not an Ed25519 private key")
    return loaded


def public_key_b64(private: ed25519.Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def export_rows(
    db: TraceDB,
    *,
    task_id: str | None = None,
    from_seq: int | None = None,
    to_seq: int | None = None,
) -> list[object]:
    clauses: list[str] = []
    params: list[object] = []
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if from_seq is not None:
        clauses.append("seq >= ?")
        params.append(from_seq)
    if to_seq is not None:
        clauses.append("seq <= ?")
        params.append(to_seq)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return list(db.fetchall(f"SELECT * FROM events{where} ORDER BY seq", params))


def record_line(row: object, *, purged: frozenset[str]) -> dict[str, object]:
    """The covered fields, the stored hash and the payload, with purged logins masked."""
    record = EventRecord.from_row(row)  # type: ignore[arg-type]
    line: dict[str, object] = record.covered()
    payload_json = row["payload_json"]  # type: ignore[index]
    payload = None if payload_json is None else parse_canonical(payload_json)
    if purged:
        line["actor"] = pseudonymise(record.actor, purged)
        payload = mask_payload(payload, purged)
    line["hash"] = str(row["hash"])  # type: ignore[index]
    line["payload"] = payload
    return line


def build_export(
    db: TraceDB,
    private: ed25519.Ed25519PrivateKey,
    *,
    task_id: str | None = None,
    from_seq: int | None = None,
    to_seq: int | None = None,
    purged: Iterable[str] = (),
    version: str | None = None,
    exported_at: str | None = None,
) -> Export:
    rows = export_rows(db, task_id=task_id, from_seq=from_seq, to_seq=to_seq)
    masked = frozenset(purged)
    lines = tuple(canonical_json(record_line(row, purged=masked)) for row in rows)
    first = int(rows[0]["seq"]) if rows else None  # type: ignore[index]
    last = int(rows[-1]["seq"]) if rows else None  # type: ignore[index]
    head = str(rows[-1]["hash"]) if rows else None  # type: ignore[index]
    trailer: dict[str, object] = {
        "headHash": head,
        "fromSeq": first,
        "toSeq": last,
        "exportedAt": exported_at or utc_now(),
        "factoryVersion": version or factory_version(),
    }
    trailer["signature"] = base64.b64encode(private.sign(canonical_bytes(trailer))).decode("ascii")
    return Export(lines, trailer, len(rows), first, last, head)


def verify_export(text: str, pubkey_b64: str) -> ExportCheck:
    """Signature, head hash, every replayable record hash, every link of a contiguous range."""
    raw_lines = [line for line in text.splitlines() if line.strip()]
    if not raw_lines:
        return ExportCheck(False, 0, None, "empty export")
    try:
        parsed = [parse_canonical(line) for line in raw_lines]
    except TraceError as exc:
        return ExportCheck(False, 0, None, f"line is not canonical JSON: {exc}")
    trailer = parsed[-1]
    if not isinstance(trailer, dict) or set(trailer) != {*TRAILER_FIELDS, "signature"}:
        return ExportCheck(False, 0, None, "trailer fields do not match")
    signature = trailer["signature"]
    unsigned = {name: trailer[name] for name in TRAILER_FIELDS}
    try:
        public = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64))
        public.verify(base64.b64decode(str(signature)), canonical_bytes(unsigned))
    except (InvalidSignature, ValueError):
        return ExportCheck(False, 0, None, "trailer signature does not verify")
    records = parsed[:-1]
    head: str | None = None
    previous_seq: int | None = None
    for line in records:
        if not isinstance(line, dict):
            return ExportCheck(False, 0, None, "record is not an object")
        try:
            stored = str(line["hash"])
            record = EventRecord(
                seq=int(line["seq"]),
                kind=str(line["kind"]),
                actor=str(line["actor"]),
                task_id=line["task_id"],
                run_id=line["run_id"],
                phase_id=line["phase_id"],
                parent_seq=line["parent_seq"],
                payload_hash=str(line["payload_hash"]),
                tokens=line["tokens"],
                started_at=line["started_at"],
                ended_at=line["ended_at"],
                created_at=str(line["created_at"]),
                prev_hash=str(line["prev_hash"]),
            )
        except (KeyError, TypeError, ValueError):
            return ExportCheck(False, 0, None, "record fields are missing")
        masked = record.actor.startswith(PSEUDONYM_PREFIX)
        if not masked and record.record_hash() != stored:
            return ExportCheck(False, 0, None, f"record hash does not match at seq {record.seq}")
        payload = line.get("payload")
        if payload is not None and not masked and payload_hash(payload) != record.payload_hash:
            return ExportCheck(False, 0, None, f"payload hash does not match at seq {record.seq}")
        if previous_seq is not None and record.seq == previous_seq + 1 and record.prev_hash != head:
            return ExportCheck(False, 0, None, f"chain link broken at seq {record.seq}")
        previous_seq = record.seq
        head = stored
    if trailer["headHash"] != head:
        return ExportCheck(False, 0, None, "trailer head hash does not match the records")
    if records and (trailer["fromSeq"] != records[0]["seq"] or trailer["toSeq"] != previous_seq):  # type: ignore[index]
        return ExportCheck(False, 0, None, "trailer seq range does not match the records")
    return ExportCheck(True, len(records), head)


def export_digest(export: Export) -> str:
    """A short handle for the export event: sha256 of the whole text."""
    return sha256_hex(export.text().encode("utf-8"))


__all__ = [
    "TRAILER_FIELDS",
    "Export",
    "ExportCheck",
    "build_export",
    "export_digest",
    "export_rows",
    "load_private_key",
    "public_key_b64",
    "record_line",
    "verify_export",
]
