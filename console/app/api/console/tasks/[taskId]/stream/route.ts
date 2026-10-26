// The live tail for the browser (TRD §5.2 streaming transport): the console proxies the
// orchestrator's SSE under the service token on behalf of the signed-in actor, so the browser
// never holds the token. Resumes from `after`; the client falls back to polling when this fails.

import { loadSession } from "@/lib/auth";
import { errorResponse } from "@/lib/bff";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request, context: { params: Promise<{ taskId: string }> }): Promise<Response> {
  const session = await loadSession();
  if (!session) return errorResponse(401, "session", "sign in to continue");
  const { taskId } = await context.params;
  const after = new URL(request.url).searchParams.get("after") ?? "0";
  const base = (process.env.ORCHESTRATOR_URL ?? "").replace(/\/$/, "");
  if (!base) return errorResponse(503, "orchestrator", "ORCHESTRATOR_URL is not set");
  const headers: Record<string, string> = { accept: "text/event-stream", "X-Factory-Actor": session.login };
  const token = process.env.FACTORY_SERVICE_TOKEN;
  if (token) headers["X-Factory-Service-Token"] = token;
  const upstream = await fetch(
    `${base}/api/v1/tasks/${encodeURIComponent(taskId)}/events/stream?after=${encodeURIComponent(after)}`,
    { headers, signal: request.signal, cache: "no-store" },
  );
  if (!upstream.ok || !upstream.body) {
    return errorResponse(upstream.status || 502, "stream", `the orchestrator answered ${upstream.status}`);
  }
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-store",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
