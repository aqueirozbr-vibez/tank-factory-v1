// SSE lag under three concurrent running tasks (TRD §5.4: a row appears ≤ 5 s after its write,
// p95 ≤ 2 s measured; §12 Load). k6 has no SSE client, so this Node script opens one stream per
// running task from each task's head seq, reads rows for SSE_SECONDS and reports the p95 of
// (arrival time − created_at) per task and overall. Exit code 1 when the overall p95 passes 5 s.
//
//   node tests/load/sse_lag.mjs            (ORCHESTRATOR_URL, SSE_SECONDS=60, SSE_TASKS=a,b,c)
const API = (process.env.ORCHESTRATOR_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const SECONDS = Number(process.env.SSE_SECONDS ?? 60);
const TASKS = (process.env.SSE_TASKS ?? "acme/atlas-api#7,acme/atlas-api#17,acme/atlas-api#27").split(",");
const HEADERS = { "X-Factory-Actor": "sse-lag" };

function p95(values) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)];
}

async function tail(taskId, lags) {
  const detail = await (await fetch(`${API}/api/v1/tasks/${encodeURIComponent(taskId)}`, { headers: HEADERS })).json();
  const after = detail.chain?.head_seq ?? 0;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SECONDS * 1000);
  try {
    const response = await fetch(`${API}/api/v1/tasks/${encodeURIComponent(taskId)}/events/stream?after=${after}`, {
      headers: { ...HEADERS, accept: "text/event-stream" },
      signal: controller.signal,
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const arrived = Date.now();
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        if (!frame.includes("event: row")) continue;
        const data = frame.split("\n").find((line) => line.startsWith("data:"))?.slice(5).trim();
        if (!data) continue;
        const row = JSON.parse(data);
        lags.push(arrived - Date.parse(row.created_at));
      }
    }
  } catch (error) {
    if (!(error instanceof Error && error.name === "AbortError")) throw error;
  } finally {
    clearTimeout(timer);
  }
}

const perTask = {};
await Promise.all(
  TASKS.map(async (taskId) => {
    const lags = [];
    perTask[taskId] = lags;
    await tail(taskId, lags);
  }),
);
const all = Object.values(perTask).flat();
const summary = {
  tasks: TASKS.length,
  seconds: SECONDS,
  rows: all.length,
  p95_ms: p95(all),
  max_ms: all.length ? Math.max(...all) : null,
  per_task: Object.fromEntries(Object.entries(perTask).map(([id, lags]) => [id, { rows: lags.length, p95_ms: p95(lags) }])),
};
console.log(`GATE-EVIDENCE load.sse_lag: ${JSON.stringify(summary)}`);
process.exit(summary.p95_ms !== null && summary.p95_ms <= 5000 && all.length > 0 ? 0 : 1);
