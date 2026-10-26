// The kill-switch, proxied to the orchestrator with the actor and an Idempotency-Key (TRD §6.1
// `POST /controls/kill-switch`: `{enabled: 0|1, reason}`; 0 fans out SIGTERM within 60 s; 409
// when already in that state). BFF order: body, session, authz (`kill_switch`), act.

import { randomUUID } from "node:crypto";

import { NextResponse } from "next/server";
import { z } from "zod";

import { postJson } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { handle } from "@/lib/bff";

export const dynamic = "force-dynamic";

export const schema = z.object({
  enabled: z.union([z.literal(0), z.literal(1)]),
  reason: z.string().min(1).max(500),
});

export async function POST(request: Request): Promise<Response> {
  return handle(request, {
    schema,
    action: "kill_switch",
    loadSession,
    act: async ({ body, session }) => {
      const result = await postJson<Record<string, unknown>>("/api/v1/controls/kill-switch", body, {
        caller: { actor: session.login },
        idempotencyKey: randomUUID(),
      });
      return NextResponse.json(result);
    },
  });
}
