// Concurrency N, proxied to the orchestrator (TRD §6.1 `POST /controls/concurrency`: `{n,
// reason}` → `{old, new, event_seq}`, N between 1 and 10). BFF order: body, session, authz
// (`concurrency`), act.

import { randomUUID } from "node:crypto";

import { NextResponse } from "next/server";
import { z } from "zod";

import { postJson } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { handle } from "@/lib/bff";

export const dynamic = "force-dynamic";

export const schema = z.object({
  n: z.number().int().min(1).max(10),
  reason: z.string().min(1).max(500),
});

export async function POST(request: Request): Promise<Response> {
  return handle(request, {
    schema,
    action: "concurrency",
    loadSession,
    act: async ({ body, session }) => {
      const result = await postJson<{ old: number; new: number; event_seq: number }>("/api/v1/controls/concurrency", body, {
        caller: { actor: session.login },
        idempotencyKey: randomUUID(),
      });
      return NextResponse.json(result);
    },
  });
}
