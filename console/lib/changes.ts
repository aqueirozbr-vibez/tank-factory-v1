// The orchestrator's `/changes` stream, listened to by the console process (TRD §5.2 cache
// keying: configuration and eval reads are tagged and revalidated on `config_loaded` and
// `eval_complete`; §6.1 `GET /changes`, one stream per console). One listener per process,
// started from instrumentation.ts; it reconnects with a backoff and never throws into a request.

import { revalidateTag } from "next/cache";

export type ChangeEvent = { event: string; data: unknown };

/** The tags a change notice invalidates (`config`, `config:<sha>`, `eval`, `eval:<run_id>`). */
export function tagsFor(event: ChangeEvent): string[] {
  const data = (event.data ?? {}) as Record<string, unknown>;
  if (event.event === "config_loaded") {
    const sha = typeof data["config_sha"] === "string" ? data["config_sha"] : null;
    return sha ? ["config", `config:${sha}`] : ["config"];
  }
  if (event.event === "eval_complete") {
    const runId = typeof data["run_id"] === "string" ? data["run_id"] : null;
    return runId ? ["eval", `eval:${runId}`] : ["eval"];
  }
  return [];
}

/** Parse complete SSE frames out of a buffer; returns the events and the unparsed remainder. */
export function parseSse(buffer: string): { events: ChangeEvent[]; rest: string } {
  const events: ChangeEvent[] = [];
  const frames = buffer.split("\n\n");
  const rest = frames.pop() ?? "";
  for (const frame of frames) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0 && event === "message") continue;
    let data: unknown = null;
    try {
      data = dataLines.length ? JSON.parse(dataLines.join("\n")) : null;
    } catch {
      data = dataLines.join("\n");
    }
    events.push({ event, data });
  }
  return { events, rest };
}

let started = false;

export function startChangesListener(options: { fetchImpl?: typeof fetch; revalidate?: (tag: string) => void } = {}): void {
  if (started) return;
  started = true;
  const base = (process.env.ORCHESTRATOR_URL ?? "").replace(/\/$/, "");
  if (!base) return; // nothing to listen to; the reads stay live
  const fetchImpl = options.fetchImpl ?? fetch;
  const revalidate = options.revalidate ?? ((tag: string) => revalidateTag(tag));
  const headers: Record<string, string> = { accept: "text/event-stream" };
  const token = process.env.FACTORY_SERVICE_TOKEN;
  if (token) headers["X-Factory-Service-Token"] = token;

  async function loop(): Promise<void> {
    let backoffMs = 1000;
    for (;;) {
      try {
        const response = await fetchImpl(`${base}/api/v1/changes`, { headers, cache: "no-store" });
        if (!response.ok || !response.body) throw new Error(`changes stream answered ${response.status}`);
        backoffMs = 1000;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSse(buffer);
          buffer = parsed.rest;
          for (const event of parsed.events) {
            for (const tag of tagsFor(event)) revalidate(tag);
          }
        }
      } catch {
        // the orchestrator is down or restarting; try again after the backoff
      }
      await new Promise((resolve) => setTimeout(resolve, backoffMs));
      backoffMs = Math.min(backoffMs * 2, 30_000);
    }
  }

  void loop();
}

/** For tests: forget that a listener started. */
export function resetChangesListener(): void {
  started = false;
}
