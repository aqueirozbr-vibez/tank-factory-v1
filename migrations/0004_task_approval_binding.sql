-- 0004_task_approval_binding: when the current spec was committed (a label
-- event before it is stale) and the governed path an approval must be owned
-- for (PRD Capability 2; TRD §6.2). Additive; the writer fills both.
ALTER TABLE tasks ADD COLUMN spec_committed_at TEXT;
ALTER TABLE tasks ADD COLUMN governed_path TEXT;
