# Unit 02: Trace schema and single writer

## Goal

Create `trace.db` from the TRD §4.2 schema through a forward-only migration
runner, and expose the only object that writes to it: `TraceWriter`. After
this unit any later unit can record installation, configuration, tasks,
runs, phases, gate results, envelopes and processes. Events and the hash
chain are unit 03.

## Design

No UI. Visible result: a temp database that opens in WAL mode with the full
schema applied once, and a writer whose methods are the schema's rows.

## Implementation

### migrations/0001_init.sql

- The TRD §4.2 schema verbatim except: no `PRAGMA` lines (the connection
  sets them); no `schema_migrations` (the runner bootstraps it); no
  `sessions` (that is `console.db`); the `config_versions` vendor CHECK
  also requires both vendors to be non-null; the `events` append-only
  update trigger covers every column, allowing only `payload_json` to go to
  NULL.

### orchestrator/trace/db.py

- `TraceDB.open(path, migrations_dir=None)`: creates the parent directory,
  connects with `isolation_level=None`, sets `journal_mode=WAL`,
  `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`, uses
  `sqlite3.Row`, bootstraps `schema_migrations`, applies every
  `NNNN_name.sql` not yet recorded, each file inside one transaction with
  its `schema_migrations` insert.
- `transaction()` context manager: `BEGIN IMMEDIATE` … `COMMIT`, rollback on
  any exception, guarded by one process lock.
- `fetchone`, `fetchall` read helpers. `close()`.
- Migrations directory: the repository's `migrations/` in development; the
  copy force-included into the wheel at `orchestrator/migrations/` when
  installed.

### orchestrator/trace/writer.py

- `TraceWriter(db)` with keyword-only methods, each wrapping one or two
  statements in a transaction: `insert_installation`, `get_installation`,
  `set_concurrency`, `set_factory_enabled`, `load_config_version`,
  `insert_task` (returns whether a row was created), `get_task`,
  `set_task_status`, `set_task_spec`, `start_run`, `finish_run` (sums
  phase tokens and cost), `start_phase`, `finish_phase` (adds cost to
  `tasks.spent_usd`), `write_gate_result` (caps `output_tail` at 4,000),
  `write_envelope`, `start_process`, `touch_process`, `end_process`.
- `sqlite3.IntegrityError` becomes `TraceError` with the constraint named.

### orchestrator/models/enums.py

- `StrEnum`s whose values are the schema's CHECK strings: `TaskStatus`,
  `BlockedReason`, `RiskClass`, `WorkspaceKind`, `RunStatus`, `PhaseName`,
  `PhaseKind`, `PhaseStatus`, `EventKind`, `GateName`, `AgentRole`,
  `OutputType`, `ProcessKind`, `ApprovalGate`, `BudgetMode`.

### orchestrator/clock.py, orchestrator/ids.py

- `utc_now()` returning ISO 8601 with milliseconds and offset; `new_id()`.

## Dependencies

- None new (sqlite3 is in the standard library).

## Verify when done

- [ ] Opening a new database applies `0001_init` once; reopening applies
      nothing and the row count in `schema_migrations` is 1
- [ ] `journal_mode` is `wal` and `foreign_keys` is on
- [ ] Inserting the installation computes `daily_cap_usd` = N × 3 × budget
      and changing N recomputes it
- [ ] A `config_versions` row with the same vendor for builder and reviewer
      is refused
- [ ] A second `insert_task` for the same issue creates no row
- [ ] A raw `runs` insert without `status` reads `fail`
- [ ] Updating an `events` row's `kind` aborts; nulling its `payload_json`
      succeeds; deleting a fresh row aborts
- [ ] `write_gate_result` stores at most 4,000 characters of output
- [ ] `v_needs_human` lists a blocked task
- [ ] The four quality commands pass
