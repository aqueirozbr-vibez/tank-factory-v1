// The one-time budget raise, proxied to the orchestrator (TRD §6.1 `POST /tasks/{id}/budget`:
// `{budget_usd, reason}` → `{old, new, event_seq}`; new ≤ 2× current, once per task; 409 already
// raised, 422 over 2×). BFF order: body, session, authz (`budget_raise`), act.

import { randomUUID } from "node:crypto";

import { NextResponse } from "next/server";
import { z } from "zod";

import { postJson, taskPath } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { handle } from "@/lib/bff";

export const dynamic = "force-dynamic";

export const schema = z.object({
  budget_usd: z.number().positive(),
  reason: z.string().min(1).max(500),
});

export async function POST(request: Request, context: { params: Promise<{ taskId: string }> }): Promise<Response> {
  const { taskId } = await context.params;
  return handle(request, {
    schema,
    action: "budget_raise",
    loadSession,
    act: async ({ body, session }) => {
      const result = await postJson<{ old: number; new: number; event_seq: number }>(taskPath(decodeURIComponent(taskId), "/budget"), body, {
        caller: { actor: session.login },
        idempotencyKey: randomUUID(),
      });
      return NextResponse.json(result);
    },
  });
}
