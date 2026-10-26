-- 0003_poll_cursors: where the reconciliation poller left off (TRD §4.5).
CREATE TABLE poll_cursors (
  name TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
