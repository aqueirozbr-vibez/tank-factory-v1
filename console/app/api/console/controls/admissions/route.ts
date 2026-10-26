// Admissions pause and resume, proxied to the orchestrator (TRD §6.1 `POST /controls/admissions`:
// `{paused: 0|1, reason}` → `{event_seq}`; `paused: 0` with `clear_incident: <seq>` also clears an
// open incident; 409 when already in that state). BFF order: body, session, authz
// (`admissions`), act.

import { randomUUID } from "node:crypto";

import { NextResponse } from "next/server";
import { z } from "zod";

import { postJson } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { handle } from "@/lib/bff";

export const dynamic = "force-dynamic";

export const schema = z.object({
  paused: z.union([z.literal(0), z.literal(1)]),
  reason: z.string().min(1).max(500),
  clear_incident: z.number().int().positive().optional(),
});

export async function POST(request: Request): Promise<Response> {
  return handle(request, {
    schema,
    action: "admissions",
    loadSession,
    act: async ({ body, session }) => {
      const result = await postJson<{ paused: boolean; event_seq: number }>("/api/v1/controls/admissions", body, {
        caller: { actor: session.login },
        idempotencyKey: randomUUID(),
      });
      return NextResponse.json(result);
    },
  });
}
