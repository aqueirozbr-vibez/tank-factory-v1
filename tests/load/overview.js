// k6: the Overview's reads against the seeded API with 1,000 tasks (TRD §5.4: Overview ≤ 2 s
// p95; §12 Load). Each virtual user is its own login (the read limit is 300 a minute per login)
// and makes the Overview's panel reads in one batch every two seconds.
//
//   k6 run -e ORCHESTRATOR_URL=http://127.0.0.1:8000 tests/load/overview.js
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 3,
  duration: "30s",
  thresholds: {
    "http_req_duration{name:overview}": ["p(95)<2000"],
    "http_req_failed{name:overview}": ["rate<0.01"],
  },
};

const API = __ENV.ORCHESTRATOR_URL || "http://127.0.0.1:8000";
const PANELS = [
  "/api/v1/tasks?limit=200",
  "/api/v1/tasks?status=running",
  "/api/v1/tasks?status=queued",
  "/api/v1/tasks?status=blocked",
  "/api/v1/kpis",
  "/api/v1/indicators",
  "/api/v1/needs-human/count",
  "/api/v1/approvals/plans",
  "/api/v1/installation",
];

export default function () {
  const params = { headers: { "X-Factory-Actor": `k6-overview-${__VU}` }, tags: { name: "overview" } };
  const responses = http.batch(PANELS.map((path) => ["GET", API + path, null, params]));
  responses.forEach((response) => check(response, { "status 200": (r) => r.status === 200 }));
  sleep(2);
}

export function handleSummary(data) {
  const metric = data.metrics["http_req_duration{name:overview}"] || data.metrics.http_req_duration;
  const p95 = metric && metric.values ? Math.round(metric.values["p(95)"]) : null;
  const failed = data.metrics["http_req_failed{name:overview}"];
  const line = `GATE-EVIDENCE load.overview: ${JSON.stringify({ vus: 3, seconds: 30, requests: data.metrics.http_reqs.values.count, p95_ms: p95, failed_rate: failed ? failed.values.rate : null })}\n`;
  return { stdout: line };
}
