-- Unit 55: Idempotency-Key storage for the write endpoints (TRD §6.1).
-- A repeated key on the same route by the same actor returns the first response.
CREATE TABLE idempotency_keys (
  key TEXT NOT NULL,
  route TEXT NOT NULL,                               -- method + path template, e.g. 'POST /tasks/{task_id}/budget'
  actor TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,                      -- the body; a different body under the same key is 422
  status INTEGER NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (key, route, actor)
);
CREATE INDEX idempotency_created ON idempotency_keys(created_at);
