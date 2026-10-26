// Start a manual eval replay, proxied to the orchestrator with the actor and an Idempotency-Key
// (TRD §6.1 `POST /eval/runs`; code-standards · Next.js BFF order). The orchestrator holds the
// 2-an-hour limit, the one-run-at-a-time rule and the 20-item floor, and answers 202 with the
// run id or 403, 409, 422.

import { randomUUID } from "node:crypto";

import { NextResponse } from "next/server";
import { z } from "zod";

import { postJson } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { handle } from "@/lib/bff";

export const dynamic = "force-dynamic";

export const schema = z.object({
  set_id: z.string().min(1),
  trigger: z.literal("manual").default("manual"),
});

export async function POST(request: Request): Promise<Response> {
  return handle(request, {
    schema,
    action: "eval_run",
    loadSession,
    act: async ({ body, session }) => {
      const result = await postJson<{ run_id: string; event_seq: number }>("/api/v1/eval/runs", body, {
        caller: { actor: session.login },
        idempotencyKey: randomUUID(),
      });
      return NextResponse.json(result, { status: 202 });
    },
  });
}
