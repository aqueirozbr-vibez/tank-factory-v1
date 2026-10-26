// A risk override before pr_open, proxied to the orchestrator with the actor and an
// Idempotency-Key (TRD §6.1 `POST /tasks/{id}/risk-override`): the orchestrator records the
// approvals row and the control event and answers 404, 409 (too late) or 403.

import { randomUUID } from "node:crypto";

import { NextResponse } from "next/server";
import { z } from "zod";

import { postJson, taskPath } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { handle } from "@/lib/bff";

export const dynamic = "force-dynamic";

export const schema = z.object({
  risk: z.enum(["risk:high", "risk:governed"]),
  reason: z.string().min(1).max(500),
});

export async function POST(request: Request, context: { params: Promise<{ taskId: string }> }): Promise<Response> {
  const { taskId } = await context.params;
  return handle(request, {
    schema,
    action: "risk_override",
    loadSession,
    act: async ({ body, session }) => {
      const result = await postJson<{ approval_id: string; event_seq: number; risk: string }>(
        taskPath(decodeURIComponent(taskId), "/risk-override"),
        body,
        { caller: { actor: session.login }, idempotencyKey: randomUUID() },
      );
      return NextResponse.json(result);
    },
  });
}
