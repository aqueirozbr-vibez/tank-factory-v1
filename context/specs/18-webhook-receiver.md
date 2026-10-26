# Unit 18: FastAPI application and webhook receiver

## Goal

Stand up the orchestrator's HTTP surface with two routes: `GET /healthz`
and `POST /webhooks/github`. The receiver verifies the HMAC signature and
the source IP, normalises the GitHub event, inserts it into `github_events`
idempotently, hands it to the in-process inbox, and answers 202. Nothing
else happens in the request (architecture.md · Invariant 20; TRD §4.5).

## Design

No UI. Visible result: webhook fixtures for `issues.labeled`,
`issues.unlabeled`, `pull_request.closed` (merged),
`pull_request_review.submitted`, `check_run.completed`,
`workflow_run.completed` and `push` each produce one normalised event; a
bad signature is 401, a foreign IP is 403, a repeated delivery is one row.

## Implementation

### orchestrator/models/events.py

- `NormalisedEvent(kind, source, github_event_id, event_type, action,
  issue_number, pr_number, actor_login, label, branch, head_sha,
  conclusion, review_state, changed_paths, received_at)` with `kind` in
  `issue_labeled`, `issue_unlabeled`, `pull_request_merged`,
  `pull_request_closed`, `review_submitted`, `check_run_completed`,
  `workflow_run_completed`, `push`, `ignored`.

### orchestrator/intake/webhook.py

- `verify_signature(secret, body, header)` (HMAC SHA-256, constant time).
- `GitHubIpRanges(cidrs, fetched_at)` with `allows(ip)`, `from_meta(json)`
  and `is_stale(now)` (daily refresh); loopback is allowed for local
  development only when `trust_loopback` is set.
- `normalise(event_type, delivery_id, payload, source)`.
- `record_delivery(writer, event)` → `True` when new (`INSERT OR IGNORE`
  on `(event_type, github_event_id)`), always writing one `webhook` or
  `poll` event with `duplicate` set.

### orchestrator/api/app.py

- `ApiDeps(writer, settings, inbox, ip_ranges, client_ip_header)` and
  `create_app(deps)`.
- `GET /healthz` → `{status, factory_enabled, db_ok, proxy_ok, last_poll_at}`.
- `POST /webhooks/github`: IP check (403), signature check (401), JSON
  parse (400), normalise, record, enqueue when new, 202
  `{accepted, duplicate, kind}`.

### Settings and writer

- `webhook_secret` (`FACTORY_WEBHOOK_SECRET`), `trusted_proxy_header`
  (`X-Forwarded-For` honoured only when set), `trust_loopback`.
- `TraceWriter.record_github_event(...) -> bool`, `set_last_poll`.

## Dependencies

- `fastapi`, `uvicorn` (the server; wired in unit 44's compose)

## Verify when done

- [ ] Each fixture normalises to its kind with the right issue or PR
      number, actor, label, branch and conclusion
- [ ] A valid signature is accepted (202) and a wrong one rejected (401)
      without a row; a source outside the hook ranges is 403
- [ ] A repeated delivery id yields `duplicate: true`, one `github_events`
      row and two `webhook` events, one marked duplicate
- [ ] The inbox receives new events only
- [ ] `/healthz` reports `db_ok` and the installation's `factory_enabled`
- [ ] The four quality commands pass
