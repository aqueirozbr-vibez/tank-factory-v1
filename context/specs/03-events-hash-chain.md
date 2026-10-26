# Unit 03: Events hash chain

## Goal

Make `events` a verifiable append-only ledger: every row is hash-chained to
its predecessor over a canonical record (TRD §4.3), retention can null a
payload without breaking the chain, and `verify` recomputes every link.
After this unit every later unit writes events through
`TraceWriter.write_event`.

## Design

No UI. Visible result: a chain of events in a temp database verifies, a
tampered row is detected, and the RFC 8785 test vector canonicalises
byte-for-byte.

## Implementation

### migrations/0002_installation_install_id.sql

- `ALTER TABLE installation ADD COLUMN install_id TEXT NOT NULL DEFAULT ''`.
  The genesis hash needs an installation identifier the TRD calls
  `install_id`; `insert_installation` fills it with a new id.

### orchestrator/trace/canonical.py

- `canonical_json(value) -> str` and `canonical_bytes(value) -> bytes`
  implementing RFC 8785: object keys sorted by UTF-16 code units, numbers
  in ES6 `Number::toString` form (integers without a fraction, `1e+21`,
  `1e-7`, `-0` as `0`), strings with the JSON short escapes and lowercase
  `\u00xx` for other control characters, no whitespace. Integers beyond
  2^53, NaN, infinities, non-string keys and non-JSON types raise
  `TraceError`.

### orchestrator/trace/chain.py

- `genesis_hash(org, repo, install_id)` =
  `sha256("factory-trace-genesis:" + org + "/" + repo + ":" + install_id)`.
- `payload_hash(payload)` = `sha256(canonical(payload))`, with `None`
  hashed as `null`.
- `EventRecord` with the covered fields `{seq, kind, actor, task_id,
  run_id, phase_id, parent_seq, payload_hash, tokens, started_at,
  ended_at, created_at, prev_hash}` and `record_hash()`.
- `append_event(db, *, kind, actor, task_id, run_id, phase_id, parent_seq,
  payload, tokens, started_at, ended_at) -> seq`: one `BEGIN IMMEDIATE`
  transaction reads the head hash (or genesis), takes the next `seq` from
  `sqlite_sequence`, stores the payload in canonical form, and inserts the
  row with its hash.
- `verify_chain(db, from_seq=None, to_seq=None) -> ChainReport(ok,
  records, head_hash, first_bad_seq, reason)`: checks each `prev_hash`
  against the predecessor (genesis for `seq` 1), recomputes each hash, and
  re-hashes any payload still present.
- `null_payloads(db, from_seq, to_seq) -> count`: the retention primitive;
  the append-only trigger permits exactly this.

### orchestrator/trace/writer.py

- `insert_installation` writes `install_id`.
- `write_event(kind, actor, *, ...) -> seq` delegates to `append_event`.

## Dependencies

- dev: `hypothesis` (property tests for the canonicaliser and the chain)

## Verify when done

- [ ] The RFC 8785 §3.2.3 example input canonicalises to the RFC's output
- [ ] ES6 number cases: `1.0 → 1`, `0.1`, `1e21 → 1e+21`, `1e-7`,
      `0.000001`, `123456789012345680000`, `5e-324`, `-0.0 → 0`
- [ ] Keys sort by UTF-16 code units (an astral key sorts before `Ａ`)
- [ ] The first event's `prev_hash` is the genesis hash; a chain of N
      events verifies with `records == N` and the head hash of the last row
- [ ] Nulling payloads in a range keeps the chain valid and `payload_hash`
      unchanged
- [ ] With the append-only trigger dropped, editing `actor` or
      `payload_json` is reported at that `seq`
- [ ] Writing an event before the installation row exists raises
- [ ] Hypothesis: any generated sequence of events verifies; canonical JSON
      round-trips through `json.loads` and is idempotent
- [ ] The four quality commands pass
