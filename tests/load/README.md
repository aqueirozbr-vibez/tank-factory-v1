# Load checks (TRD §5.4 budgets, §12 Load; build plan unit 66)

Against the seeded API (`uv run python tests/e2e/serve_seeded_api.py <data_dir> 8000`),
or an installation's orchestrator with a service token added to the headers:

| Check | Command | Budget |
| --- | --- | --- |
| Overview with 1,000 tasks | `k6 run -e ORCHESTRATOR_URL=http://127.0.0.1:8000 tests/load/overview.js` | p95 < 2 s |
| Task detail with 10,000 rows | `k6 run -e ORCHESTRATOR_URL=http://127.0.0.1:8000 tests/load/task_detail.js` | p95 < 2 s |
| Admission, 50 label events in a minute | `k6 run -e ORCHESTRATOR_URL=http://127.0.0.1:8000 -e WEBHOOK_SECRET=e2e-webhook-secret tests/load/admission.js` | p95 < 2 min |
| SSE lag under 3 running tasks | `node tests/load/sse_lag.mjs` (`SSE_SECONDS=60`) | p95 ≤ 5 s (2 s measured) |

Each script prints one `GATE-EVIDENCE` line; the tracker carries the numbers. k6 is the
Grafana binary (`winget install GrafanaLabs.k6`, or the release zip); the SSE check is a Node
script because k6 has no SSE client. The read limits are 300 a minute per login, so each
virtual user is its own login and paces itself.
