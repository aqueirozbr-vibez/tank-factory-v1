# Unit 53: Read API and streams

## Goal

Every read endpoint of TRD §6.1 under `/api/v1` (`/installation`,
`/tasks`, `/tasks/{id}`, `/events`, `/envelopes`, `/gates`, `/review`,
`/approvals/plans`, `/approvals/questions`, `/audit/queue`, `/kpis`,
`/indicators`, `/needs-human/count`, `/eval/sets`, `/eval/runs`,
`/eval/runs/{id}`, `/config`, `/config/history`), the SSE streams
`/tasks/{id}/events/stream` and `/changes`, cursor paging at 200 rows, the
standard error body, the TRD rate limits, and the committed OpenAPI
document with its contract test (PRD §Capability 6; TRD §5.2, §6.1;
code-standards · API Routes).

## Design

Terminal only. `models/api.py` holds the response schemas (lists are
`{items, next}`; errors are `{code, message, detail?, traceId}`), and the
OpenAPI document is generated from them (`orchestrator/api/openapi.py`,
`factory openapi [--check]`, committed as `openapi.json`). `api/limits.py`
identifies the caller: the console's service token (`FACTORY_SERVICE_TOKEN`,
32 random bytes written by `factory install`) names the actor through
`X-Factory-Actor`; a loopback caller stands in until the console lands;
anyone else gets 403. Token buckets per login (or address) carry the TRD
limits per rule (300/min reads, 600/min the count, 60/min audit, 10/h
export, 10/min writes, 1/min kill-switch, 2/h eval runs) and five open
tail streams per login; a limited request answers 429 naming the rule.
`api/read.py` serves trace rows through the writer with JSON columns
parsed and purged logins masked: the task detail carries runs, phases,
approvals, the budget view, GitHub links and the chain footer (the chain
verified over the task's seq span plus its two neighbours); `/review`
reads the `verdict_policy` log of each round and the reviewer envelope;
`/approvals/plans` lists running `plan_approval` phases with the spec
text from the clone (`git show branch:path`), the risk breakdown and the
AC→holdout checks; `/approvals/questions` parses the blocked task's
questions and detects their language; `/kpis` and `/indicators` serve the
newest `kpi_daily` day with the PRD targets and the indicator lines beside
each series; `/needs-human/count` splits blocked tasks into questions,
decisions and budget plus the running plan approvals. `api/streams.py`
polls the trace every 0.5 s: the tail emits `event: row` with `id: seq`
(resumable through `after=`) and `event: ping` after 15 s of silence;
`/changes` emits `task_changed`, `config_loaded` and `eval_complete`
notices. The three write endpoints of earlier units stay where they are
until unit 55 moves them under the prefix with the authz check.

## Implementation

- `orchestrator/models/api.py`; `orchestrator/api/{limits,read,streams,openapi}.py`;
  `create_app` mounts the router at `/api/v1`; `ApiDeps(repo_clone=, limiter=)`.
- `Settings.service_token`; `factory install` writes `FACTORY_SERVICE_TOKEN`;
  `.env.example`; `cli/openapi_cmd.py` and `openapi.json`.
- Tests: `tests/unit/test_read_api.py` (every endpoint, paging, errors,
  limits, the tail latency), `tests/unit/test_openapi.py` (the committed
  document matches), `tests/integration/test_api_contract.py`
  (schemathesis over the schema), `tests/integration/test_api_perf.py`
  (Overview reads under 2 s with 1,000 seeded tasks).

## Dependencies

- Units 47 and 48. New dev dependency: `schemathesis` (the plan names it).

## Verify when done

- [ ] Every endpoint answers with its model; unknown ids 404; bad params
      400; an anonymous caller 403; a limited caller 429 naming the rule
- [ ] A row written to the trace appears on the tail within 2 s; the tail
      resumes from `after`; `/changes` reports a task change and an eval
- [ ] `openapi.json` matches the application; schemathesis is green
- [ ] Overview reads under 2 s with 1,000 seeded tasks
- [ ] The four quality commands pass
