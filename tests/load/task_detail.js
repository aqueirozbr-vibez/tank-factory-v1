// k6: the Task detail's reads for the task with 10,000 trace rows (TRD §5.4: Task detail ≤ 2 s
// p95 with 10,000 rows; §12 Load): the detail (with its chain verification), the newest page of
// events before the head, the gates and the envelopes, one login per virtual user.
//
//   k6 run -e ORCHESTRATOR_URL=http://127.0.0.1:8000 tests/load/task_detail.js
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 3,
  duration: "30s",
  thresholds: {
    "http_req_duration{name:task_detail}": ["p(95)<2000"],
    "http_req_failed{name:task_detail}": ["rate<0.01"],
  },
};

const API = __ENV.ORCHESTRATOR_URL || "http://127.0.0.1:8000";
const TASK = encodeURIComponent(__ENV.TASK_ID || "acme/atlas-api#7");

export default function () {
  const params = { headers: { "X-Factory-Actor": `k6-detail-${__VU}` }, tags: { name: "task_detail" } };
  const detail = http.get(`${API}/api/v1/tasks/${TASK}`, params);
  check(detail, { "detail 200": (r) => r.status === 200 });
  let head = 0;
  try {
    head = detail.json("chain.head_seq") || 0;
  } catch (_) {
    head = 0;
  }
  const rest = http.batch([
    ["GET", `${API}/api/v1/tasks/${TASK}/events?before=${head + 1}&limit=200`, null, params],
    ["GET", `${API}/api/v1/tasks/${TASK}/gates`, null, params],
    ["GET", `${API}/api/v1/tasks/${TASK}/envelopes`, null, params],
  ]);
  rest.forEach((response) => check(response, { "status 200": (r) => r.status === 200 }));
  sleep(2);
}

export function handleSummary(data) {
  const metric = data.metrics["http_req_duration{name:task_detail}"] || data.metrics.http_req_duration;
  const p95 = metric && metric.values ? Math.round(metric.values["p(95)"]) : null;
  const failed = data.metrics["http_req_failed{name:task_detail}"];
  const line = `GATE-EVIDENCE load.task_detail: ${JSON.stringify({ vus: 3, seconds: 30, requests: data.metrics.http_reqs.values.count, p95_ms: p95, failed_rate: failed ? failed.values.rate : null })}\n`;
  return { stdout: line };
}
