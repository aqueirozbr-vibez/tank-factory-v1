// One audit decision, proxied to the orchestrator with the service token, the actor and an
// Idempotency-Key (TRD §6.1 `POST /audit/{task_id}`; code-standards · Next.js BFF order). The
// orchestrator keeps the one-decision-per-task-per-month rule and answers 404, 409 or 403.

import { randomUUID } from "node:crypto";

import { NextResponse } from "next/server";
import { z } from "zod";

import { postJson } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { handle } from "@/lib/bff";

export const dynamic = "force-dynamic";

export const schema = z.object({
  decision: z.enum(["agree", "disagree"]),
  reason: z.string().min(1).max(500),
  month: z
    .string()
    .regex(/^\d{4}-\d{2}$/)
    .optional(),
});

export async function POST(request: Request, context: { params: Promise<{ taskId: string }> }): Promise<Response> {
  const { taskId } = await context.params;
  return handle(request, {
    schema,
    action: "audit_decide",
    loadSession,
    act: async ({ body, session }) => {
      const result = await postJson<{ event_seq: number; month: string; decision: string }>(
        `/api/v1/audit/${encodeURIComponent(decodeURIComponent(taskId))}`,
        body,
        { caller: { actor: session.login }, idempotencyKey: randomUUID() },
      );
      return NextResponse.json(result);
    },
  });
}
