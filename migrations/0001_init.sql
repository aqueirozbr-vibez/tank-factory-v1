-- 0001_init: the trace schema from TRD §4.2.
-- Pragmas are set by the connection; schema_migrations is bootstrapped by the
-- runner; console.db sessions live in the console, never here.

-- ----- installation and configuration -----
CREATE TABLE installation (
  id INTEGER PRIMARY KEY CHECK (id = 1),            -- singleton: one organisation per installation
  org TEXT NOT NULL,
  repo TEXT NOT NULL,
  factory_version TEXT NOT NULL,                     -- pinned tag of the factory repository
  config_sha TEXT NOT NULL,                          -- current .factory/ tree sha on main
  concurrency_n INTEGER NOT NULL DEFAULT 3 CHECK (concurrency_n BETWEEN 1 AND 10),
  budget_mode TEXT NOT NULL DEFAULT 'install' CHECK (budget_mode IN ('install','median')),
  per_task_budget_usd REAL NOT NULL CHECK (per_task_budget_usd > 0),
  daily_cap_usd REAL NOT NULL,                       -- always concurrency_n × 3 × per_task_budget_usd
  retention_days INTEGER NOT NULL DEFAULT 90 CHECK (retention_days BETWEEN 30 AND 365),
  factory_enabled INTEGER NOT NULL DEFAULT 1 CHECK (factory_enabled IN (0,1)),
  admissions_paused INTEGER NOT NULL DEFAULT 0 CHECK (admissions_paused IN (0,1)),
  export_pubkey TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TRIGGER installation_daily_cap AFTER UPDATE OF concurrency_n, per_task_budget_usd ON installation
BEGIN
  UPDATE installation SET daily_cap_usd = NEW.concurrency_n * 3 * NEW.per_task_budget_usd WHERE id = 1;
END;
CREATE TRIGGER installation_daily_cap_ins AFTER INSERT ON installation
BEGIN
  UPDATE installation SET daily_cap_usd = NEW.concurrency_n * 3 * NEW.per_task_budget_usd WHERE id = 1;
END;

CREATE TABLE config_versions (                       -- one row per .factory/ tree the orchestrator has loaded
  config_sha TEXT PRIMARY KEY,
  main_commit TEXT NOT NULL,
  roster_json TEXT NOT NULL,
  risk_yaml TEXT NOT NULL,
  review_policy_yaml TEXT NOT NULL,
  protected_paths_json TEXT NOT NULL,
  quality_commands_json TEXT NOT NULL,
  vendors_json TEXT NOT NULL,                        -- derived from roster; two vendors enforced below
  loaded_at TEXT NOT NULL,
  CHECK (
    json_extract(vendors_json, '$.builder') IS NOT NULL
    AND json_extract(vendors_json, '$.reviewer') IS NOT NULL
    AND json_extract(vendors_json, '$.builder') <> json_extract(vendors_json, '$.reviewer')
  )
);

CREATE TABLE user_permissions (                      -- cache of GitHub collaborator permission, TTL 5 min
  login TEXT PRIMARY KEY,
  permission TEXT NOT NULL CHECK (permission IN ('admin','maintain','write','triage','read','none')),
  codeowner_paths_json TEXT NOT NULL DEFAULT '[]',
  checked_at TEXT NOT NULL
);

-- ----- tasks and runs -----
CREATE TABLE tasks (
  task_id TEXT PRIMARY KEY,                          -- 'owner/repo#418' — the idempotency key (PRD Cap. 1)
  issue_number INTEGER NOT NULL UNIQUE,
  title TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued','running','blocked','pr_open','merged','cancelled')),
  blocked_reason TEXT CHECK (blocked_reason IS NULL OR blocked_reason IN (
    'open_questions','missing_holdout','spec_gate','protected_path','repair_cap','revision_cap',
    'human_decision_needed','budget','timeout','dependency_change','full_suite')),
  risk TEXT CHECK (risk IS NULL OR risk IN ('risk:low','risk:high','risk:governed')),
  risk_score REAL,
  risk_overridden_by TEXT,
  branch TEXT NOT NULL,                              -- 'agent/418'
  workspace_kind TEXT NOT NULL CHECK (workspace_kind IN ('worktree','container')),
  spec_path TEXT,
  spec_sha TEXT,                                     -- approval is bound to spec_sha (PRD Cap. 2)
  pr_number INTEGER,
  pr_head_sha TEXT,
  budget_usd REAL NOT NULL,
  spent_usd REAL NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 1,
  admitted_by TEXT NOT NULL,                         -- login of the factory:ready actor
  config_sha TEXT NOT NULL REFERENCES config_versions(config_sha),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX tasks_status ON tasks(status, created_at);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  attempt INTEGER NOT NULL,
  workflow TEXT NOT NULL DEFAULT 'issue_to_pr',
  factory_version TEXT NOT NULL,
  config_sha TEXT NOT NULL REFERENCES config_versions(config_sha),
  status TEXT NOT NULL DEFAULT 'fail' CHECK (status IN ('running','success','fail')),  -- success must be earned
  started_at TEXT NOT NULL,
  ended_at TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  total_cost_usd REAL NOT NULL DEFAULT 0,
  UNIQUE (task_id, attempt)
);

CREATE TABLE phases (
  phase_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  seq INTEGER NOT NULL,
  name TEXT NOT NULL CHECK (name IN (
    'admission','plan','spec_gate','risk','plan_approval','build','code_gates','commit',
    'review','revision','full_suite','deliver','ci_wait')),
  kind TEXT NOT NULL CHECK (kind IN ('engineer','agent','code')),
  owner TEXT NOT NULL,                               -- 'planner' | 'builder' | 'reviewer' | 'orchestrator' | login
  status TEXT NOT NULL DEFAULT 'fail' CHECK (status IN ('running','pass','fail','skipped')),
  attempt INTEGER NOT NULL DEFAULT 1,
  retries INTEGER NOT NULL DEFAULT 0,
  harness TEXT,
  harness_version TEXT,
  model_id TEXT,
  session_id TEXT,
  virtual_key_id TEXT,
  timeout_s INTEGER NOT NULL,
  phase_cap_usd REAL,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  tokens_cache_read INTEGER DEFAULT 0,
  tokens_cache_write INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  error TEXT,
  UNIQUE (run_id, seq)
);

-- ----- trace -----
CREATE TABLE events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT REFERENCES runs(run_id),
  phase_id TEXT REFERENCES phases(phase_id),
  task_id TEXT REFERENCES tasks(task_id),
  parent_seq INTEGER REFERENCES events(seq),
  kind TEXT NOT NULL CHECK (kind IN (
    'phase_start','phase_end','agent_start','agent_end','tool_call','handoff','gate_pass','gate_fail',
    'log','error','webhook','poll','label_write','comment_write','control','approval','audit',
    'egress_denied','budget_stop','purge','export','incident')),
  actor TEXT NOT NULL,                               -- role, 'orchestrator', or a login for control/approval/audit
  payload_json TEXT,                                 -- nulled by retention
  payload_hash TEXT NOT NULL,                        -- sha256 of the canonical payload; survives retention (TRD §4.3)
  tokens INTEGER,
  started_at TEXT,
  ended_at TEXT,
  created_at TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE
);
CREATE INDEX events_phase ON events(phase_id, seq);
CREATE INDEX events_task ON events(task_id, seq);
CREATE INDEX events_kind ON events(kind, created_at);
CREATE TRIGGER events_append_only_u BEFORE UPDATE ON events
  WHEN NEW.seq IS NOT OLD.seq
    OR NEW.run_id IS NOT OLD.run_id OR NEW.phase_id IS NOT OLD.phase_id OR NEW.task_id IS NOT OLD.task_id
    OR NEW.parent_seq IS NOT OLD.parent_seq OR NEW.kind IS NOT OLD.kind OR NEW.actor IS NOT OLD.actor
    OR NEW.payload_hash IS NOT OLD.payload_hash OR NEW.tokens IS NOT OLD.tokens
    OR NEW.started_at IS NOT OLD.started_at OR NEW.ended_at IS NOT OLD.ended_at
    OR NEW.created_at IS NOT OLD.created_at OR NEW.prev_hash IS NOT OLD.prev_hash OR NEW.hash IS NOT OLD.hash
    OR (NEW.payload_json IS NOT NULL AND NEW.payload_json IS NOT OLD.payload_json)
BEGIN
  SELECT RAISE(ABORT, 'events is append-only; retention may only null payload_json');
END;
CREATE TRIGGER events_append_only_d BEFORE DELETE ON events
  WHEN julianday('now') - julianday(OLD.created_at) < 365
BEGIN
  SELECT RAISE(ABORT, 'events kept 1 year');
END;

CREATE TABLE envelopes (
  envelope_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  phase_id TEXT NOT NULL REFERENCES phases(phase_id),
  agent TEXT NOT NULL CHECK (agent IN ('planner','builder','reviewer')),
  output_type TEXT NOT NULL CHECK (output_type IN ('PlanOutput','BuildOutput','ReviewOutput','VerifyOutput')),
  payload_json TEXT,
  valid INTEGER NOT NULL CHECK (valid IN (0,1)),
  violations_json TEXT,
  attempt INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE gate_results (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  phase_id TEXT NOT NULL REFERENCES phases(phase_id),
  attempt INTEGER NOT NULL,
  gate TEXT NOT NULL CHECK (gate IN (
    'envelope_schema','artifacts_exist','files_non_empty','diff_matches_claims','permissions_enforce',
    'spec_sections','spec_ears','spec_ac_holdout_map','spec_adr_cited',
    'format','lint','typecheck','focused_tests','verdict_consistent','full_suite')),
  passed INTEGER NOT NULL CHECK (passed IN (0,1)),
  checks_json TEXT NOT NULL,                         -- [{item, ok, note}] — evidence, not a verdict
  violations_json TEXT,
  command TEXT,
  exit_code INTEGER,
  output_tail TEXT,                                  -- ≤ 4,000 chars, capped by the writer
  created_at TEXT NOT NULL
);
CREATE INDEX gate_results_gate ON gate_results(gate, passed, created_at);

CREATE TABLE processes (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  phase_id TEXT REFERENCES phases(phase_id),
  kind TEXT NOT NULL CHECK (kind IN ('agent','command','container')),
  name TEXT NOT NULL,
  pid INTEGER,
  container_id TEXT,
  command TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  exit_code INTEGER,
  last_event_at TEXT                                 -- stuck = running and now − last_event_at > 15 min
);

-- ----- approvals and audits -----
CREATE TABLE approvals (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  phase_id TEXT REFERENCES phases(phase_id),
  gate TEXT NOT NULL CHECK (gate IN ('plan','governed','risk_override')),
  actor_login TEXT NOT NULL,                         -- from the GitHub label event, never from the console request
  bound_sha TEXT NOT NULL,                           -- spec file sha the approval covers
  governed_path TEXT,
  codeowner INTEGER CHECK (codeowner IS NULL OR codeowner IN (0,1)),
  reason TEXT,
  github_label TEXT NOT NULL DEFAULT 'factory:plan-approved',
  github_event_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (task_id, gate, bound_sha)                  -- a later spec edit needs a new approval
);

CREATE TABLE audit_decisions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  pr_number INTEGER NOT NULL,
  month TEXT NOT NULL,                               -- '2026-10'
  stratum TEXT NOT NULL CHECK (stratum IN ('risk:low','risk:high','risk:governed')),
  sampled INTEGER NOT NULL CHECK (sampled IN (0,1)), -- low: 10% random; high and governed: every PR
  decision TEXT CHECK (decision IS NULL OR decision IN ('agree','disagree')),
  reason TEXT,
  decided_by TEXT,
  decided_at TEXT,
  event_seq INTEGER REFERENCES events(seq),
  UNIQUE (task_id, month)
);

CREATE TABLE github_events (                         -- intake idempotency for webhooks and the poller
  source TEXT NOT NULL CHECK (source IN ('webhook','poll')),
  github_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  issue_number INTEGER,
  received_at TEXT NOT NULL,
  processed_at TEXT,
  PRIMARY KEY (event_type, github_event_id)
);

-- ----- evaluation -----
CREATE TABLE eval_sets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  repo TEXT NOT NULL,
  reference_pass_rate REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE eval_items (
  id TEXT PRIMARY KEY,
  set_id TEXT NOT NULL REFERENCES eval_sets(id),
  issue_number INTEGER NOT NULL,
  class TEXT NOT NULL CHECK (class IN ('bug','test','docs','feature')),
  merged_pr INTEGER,
  UNIQUE (set_id, issue_number)
);
CREATE TABLE eval_runs (
  id TEXT PRIMARY KEY,
  set_id TEXT NOT NULL REFERENCES eval_sets(id),
  factory_version TEXT NOT NULL,
  config_sha TEXT NOT NULL REFERENCES config_versions(config_sha),
  trigger TEXT NOT NULL CHECK (trigger IN ('install_dry_run','config_pr','factory_upgrade','manual','harness_upgrade')),
  trigger_ref TEXT,                                  -- PR number or tag
  baseline_run_id TEXT REFERENCES eval_runs(id),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','complete','interrupted')),
  pass_rate REAL,
  per_class_json TEXT,
  delta_points REAL,
  blocked INTEGER CHECK (blocked IS NULL OR blocked IN (0,1))  -- 1 when delta_points < -10
);
CREATE TABLE eval_results (
  run_id TEXT NOT NULL REFERENCES eval_runs(id),
  item_id TEXT NOT NULL REFERENCES eval_items(id),
  passed INTEGER NOT NULL CHECK (passed IN (0,1)),
  cost_usd REAL,
  duration_s INTEGER,
  failure_reason TEXT,
  PRIMARY KEY (run_id, item_id)
);

-- ----- derived -----
CREATE TABLE kpi_daily (
  day TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL,
  numerator REAL,
  denominator REAL,
  PRIMARY KEY (day, metric)
);
CREATE VIEW v_needs_human AS
  SELECT task_id, status, blocked_reason, risk, updated_at FROM tasks
  WHERE status = 'blocked'
     OR (status = 'running' AND EXISTS (
          SELECT 1 FROM phases p JOIN runs r ON r.run_id = p.run_id
          WHERE r.task_id = tasks.task_id AND p.name = 'plan_approval' AND p.status = 'running'));
CREATE VIEW v_audit_queue AS
  SELECT a.*, t.risk, t.pr_number AS pr FROM audit_decisions a JOIN tasks t USING (task_id)
  WHERE a.sampled = 1 AND a.decision IS NULL;
CREATE VIEW v_gate_fail_distribution AS
  SELECT gate, COUNT(*) AS fails FROM gate_results
  WHERE passed = 0 AND created_at > datetime('now','-30 days') GROUP BY gate;
