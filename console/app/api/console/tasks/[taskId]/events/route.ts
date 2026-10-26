// A read proxy for the trace pages the browser asks for (TRD §3.4, §5.2): the poll fallback
// (`after=`) and the earlier pages behind the newest one (`before=`). The session gates it; the
// orchestrator answers with the service token on behalf of the signed-in actor.

import { NextResponse } from "next/server";

import { ApiError, getJson, taskPath } from "@/lib/api";
import type { components } from "@/lib/api.types";
import { loadSession } from "@/lib/auth";
import { errorResponse } from "@/lib/bff";

export const dynamic = "force-dynamic";

const PASS = ["after", "before", "limit", "kind"] as const;

export async function GET(request: Request, context: { params: Promise<{ taskId: string }> }): Promise<Response> {
  const session = await loadSession();
  if (!session) return errorResponse(401, "session", "sign in to continue");
  const { taskId } = await context.params;
  const params = new URL(request.url).searchParams;
  const query: Record<string, string> = {};
  for (const key of PASS) {
    const value = params.get(key);
    if (value !== null) query[key] = value;
  }
  try {
    const page = await getJson<components["schemas"]["EventPage"]>(taskPath(decodeURIComponent(taskId), "/events"), {
      caller: { actor: session.login },
      query,
    });
    return NextResponse.json(page, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof ApiError) return errorResponse(error.status, error.body.code, error.body.message);
    return errorResponse(502, "upstream", "the orchestrator is unreachable");
  }
}
