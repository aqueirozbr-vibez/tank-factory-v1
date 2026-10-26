# Unit 51: Retention, purge and signed export

## Goal

The nightly retention job at 03:00 installation time nulls payloads older
than `retention_days` and appends one `purge` event per day; metadata is
deleted at one year; `factory purge --task T` and `--login L` (the login
shown as `user:<sha256 prefix>` from the request on); the Ed25519 export
key from install signs `GET /tasks/{id}/export` JSONL and `factory export`
(PRD §Non-functional · Privacy, · Auditability; TRD §4.3, §4.4, §6.1).

## Design

Terminal only. `trace/retention.py`: `null_before(cutoff)` sets
`events.payload_json`, `envelopes.payload_json` and
`gate_results.output_tail` to NULL for rows older than the cutoff and
returns the seq range; `delete_metadata(cutoff)` deletes events older than
the year plus one day (clear of the table's own delete trigger) that no
child event or audit decision references, then phases and runs older than
the year that no event or approval references, with their processes,
envelopes and gate results; `tasks` rows are never deleted. `RetentionJob`
runs once a day from 03:00 installation time (the last `purge` event's
`day` is the guard) and writes one `purge` event with the counts, the seq
range and both cutoffs. `purge_task` nulls one task's payloads at once and
writes a `purge` event under the operator's login; `purge_login` writes a
`purge` event carrying only the login's SHA-256, and readers
(`pseudonymise`, `purged_logins`) show `user:<12 hex>` for that login from
then on: the `factory trace` listing and the export. No hash-covered
column is rewritten, so `verify_chain` passes after every purge.

`trace/export.py`: `build_export` writes one canonical line per event (the
covered record, the stored `hash`, the payload or `null`) and the trailer
`{headHash, fromSeq, toSeq, exportedAt, factoryVersion, signature}` where
the signature is Ed25519 over the canonical bytes of the five fields;
`verify_export` checks the signature against the installation's public
key, the head hash and seq range, every replayable record hash and payload
hash, and every link of a contiguous range. A pseudonymised line keeps its
stored hash, which cannot be replayed without the login (by design).
`GET /tasks/{task_id:path}/export` accepts a loopback caller with
`X-Factory-Actor` until the console token lands, answers 404 for an
unknown task and 503 without a key, records an `export` event and returns
`application/x-ndjson`. `Settings.export_key_file`
(`FACTORY_EXPORT_KEY_FILE`) names the key; `factory install` writes it to
`/etc/factory/export.key` and the env file. The service runs the retention
loop beside the KPI, budget and watchdog loops.

## Implementation

- `orchestrator/trace/retention.py`, `orchestrator/trace/export.py`.
- `cli/retention_cmd.py`: `factory purge --task T | --login L [--by]`,
  `factory export [--task T | --from --to] [--out FILE] [--key PEM] [--by]`.
- `api/app.py` export route; `service.py` loop; `settings.py`,
  `cli/install.py` env, `.env.example`; `cli/trace_cmd.py` masks logins.

## Dependencies

- Unit 03 (chain), 44 (the key at install). `cryptography` (present).

## Verify when done

- [ ] After the nightly job and after a task purge the chain still verifies;
      the job runs once a day from 03:00; old metadata is deleted and a
      phase an approval references stays
- [ ] An export verifies with the public key recorded at install; a
      tampered line, a tampered signature and a foreign key fail
- [ ] A purged login shows as `user:<prefix>` in the trace listing and the
      export, never in the purge event
- [ ] The route answers 403, 400, 404, 503 and 200 as designed; the CLI
      round trip verifies
- [ ] The four quality commands pass
