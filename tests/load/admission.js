// k6: admission latency with 50 label events in a minute (TRD §5.4: admission ≤ 2 min p95 after
// the label event; §12 Load). Each iteration signs one `issues.labeled factory:ready` delivery
// with the webhook secret, posts it to `/webhooks/github`, and polls the task read until the
// task exists. Against tests/e2e/serve_seeded_api.py the admission is the fixture's stand-in
// (a `tasks` row per label); on an installation it is the intake's, which also reads the issue.
//
//   k6 run -e ORCHESTRATOR_URL=http://127.0.0.1:8000 -e WEBHOOK_SECRET=e2e-webhook-secret tests/load/admission.js
import http from "k6/http";
import { check, sleep } from "k6";
import crypto from "k6/crypto";
import { Rate, Trend } from "k6/metrics";

export const options = {
  vus: 1,
  iterations: 50,
  thresholds: { admission_latency: ["p(95)<120000"], webhook_accepted: ["rate>0.99"] },
};

const latency = new Trend("admission_latency", true);
const accepted = new Rate("webhook_accepted");
const API = __ENV.ORCHESTRATOR_URL || "http://127.0.0.1:8000";
const SECRET = __ENV.WEBHOOK_SECRET || "e2e-webhook-secret";
const BASE_ISSUE = Number(__ENV.BASE_ISSUE || 5000) + (Date.now() % 100000) * 100;
const RUN = `${Date.now()}`;

export default function () {
  const issue = BASE_ISSUE + __ITER;
  const body = JSON.stringify({
    action: "labeled",
    issue: { number: issue, title: `load issue ${issue}`, labels: [{ name: "factory:ready" }] },
    label: { name: "factory:ready" },
    sender: { login: "k6" },
    repository: { full_name: "acme/atlas-api" },
  });
  const signature = "sha256=" + crypto.hmac("sha256", SECRET, body, "hex");
  const started = Date.now();
  const delivered = http.post(`${API}/webhooks/github`, body, {
    headers: {
      "content-type": "application/json",
      "X-GitHub-Event": "issues",
      "X-GitHub-Delivery": `k6-${RUN}-${__ITER}`,
      "X-Hub-Signature-256": signature,
    },
    tags: { name: "webhook" },
  });
  accepted.add(delivered.status === 202);
  check(delivered, { "202 accepted": (r) => r.status === 202 });
  const taskUrl = `${API}/api/v1/tasks/${encodeURIComponent(`acme/atlas-api#${issue}`)}`;
  let admitted = false;
  for (let attempt = 0; attempt < 100 && !admitted; attempt += 1) {
    const task = http.get(taskUrl, { headers: { "X-Factory-Actor": "k6-admission" }, tags: { name: "poll" } });
    if (task.status === 200) admitted = true;
    else sleep(0.1);
  }
  latency.add(Date.now() - started);
  check(admitted, { "task admitted": (seen) => seen });
  sleep(1.1); // 50 events spread over about a minute
}

export function handleSummary(data) {
  const metric = data.metrics.admission_latency;
  const line = `GATE-EVIDENCE load.admission: ${JSON.stringify({
    events: 50,
    p95_ms: metric ? Math.round(metric.values["p(95)"]) : null,
    max_ms: metric ? Math.round(metric.values.max) : null,
    accepted_rate: data.metrics.webhook_accepted ? data.metrics.webhook_accepted.values.rate : null,
  })}\n`;
  return { stdout: line };
}
