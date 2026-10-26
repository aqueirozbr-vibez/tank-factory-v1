// A read proxy for the nav badge (TRD §5.2): the session gates it; the orchestrator answers with
// the service token on behalf of the signed-in actor. No body, no write, no authz row beyond read.

import { NextResponse } from "next/server";

import { ApiError, read } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { errorResponse } from "@/lib/bff";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const session = await loadSession();
  if (!session) return errorResponse(401, "session", "sign in to continue");
  try {
    const counts = await read("/api/v1/needs-human/count", { caller: { actor: session.login } });
    return NextResponse.json(counts, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof ApiError) return errorResponse(error.status, error.body.code, error.body.message);
    return errorResponse(502, "upstream", "the orchestrator is unreachable");
  }
}
