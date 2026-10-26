import { describe, expect, it } from "vitest";
import { z } from "zod";

import { ApiError } from "./api";
import { denialReason, handle } from "./bff";
import type { SessionRow } from "./sessions";

const schema = z.object({ issue: z.number().int().positive(), reason: z.string().min(1) });

function session(permission: string): SessionRow {
  return {
    sid: "s1",
    login: "rsouza",
    permission,
    created_at: "2026-10-24T12:00:00.000Z",
    expires_at: "2026-10-24T20:00:00.000Z",
  };
}

function request(body: unknown): Request {
  return new Request("http://console.test/api/console/labels", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

describe("BFF handler order", () => {
  it("validates, then needs a session, then the authz table, then acts", async () => {
    const acted: string[] = [];
    const spec = {
      schema,
      action: "plan_approve",
      loadSession: async () => session("write"),
      act: async ({ body, session: s }: { body: z.infer<typeof schema>; session: SessionRow }) => {
        acted.push(`${s.login}:${body.issue}`);
        return Response.json({ ok: true });
      },
    };
    const bad = await handle(request("{not json"), spec);
    expect(bad.status).toBe(400);
    const shape = (await bad.json()) as { code: string; message: string; traceId: string };
    expect(shape.code).toBe("body");
    expect(shape.traceId).toMatch(/^[0-9a-f]+$/);

    const invalid = await handle(request({ issue: -1 }), spec);
    expect(invalid.status).toBe(400);

    const noSession = await handle(request({ issue: 418, reason: "ok" }), {
      ...spec,
      loadSession: async () => null,
    });
    expect(noSession.status).toBe(401);

    const reader = await handle(request({ issue: 418, reason: "ok" }), {
      ...spec,
      loadSession: async () => session("read"),
    });
    expect(reader.status).toBe(403);
    expect(((await reader.json()) as { code: string }).code).toBe("plan_approve_denied");

    const ok = await handle(request({ issue: 418, reason: "ok" }), spec);
    expect(ok.status).toBe(200);
    expect(acted).toEqual(["rsouza:418"]);
  });

  it("asks the CODEOWNER question only for governed approvals", async () => {
    const spec = {
      schema,
      action: "governed_approve",
      loadSession: async () => session("write"),
      codeowner: async () => false,
      act: async () => Response.json({ ok: true }),
    };
    const denied = await handle(request({ issue: 1, reason: "r" }), spec);
    expect(denied.status).toBe(403);
    expect(((await denied.json()) as { message: string }).message).toContain("CODEOWNER");
    const owner = await handle(request({ issue: 1, reason: "r" }), { ...spec, codeowner: async () => true });
    expect(owner.status).toBe(200);
    expect(denialReason("write", "governed_approve", false)).toBe("this task's governed path needs a CODEOWNER");
    expect(denialReason("read", "cancel", false)).toBe("your permission (read) does not allow cancel");
  });

  it("resolves the action from the body and hands it to the act step", async () => {
    const seen: string[] = [];
    const spec = {
      schema,
      action: async (body: z.infer<typeof schema>) => (body.issue === 7 ? "governed_approve" : "plan_approve"),
      loadSession: async () => session("write"),
      codeowner: async (_body: unknown, _s: SessionRow, action: string) => action === "governed_approve",
      act: async ({ action }: { action: string }) => {
        seen.push(action);
        return Response.json({ ok: true });
      },
    };
    expect((await handle(request({ issue: 7, reason: "r" }), spec)).status).toBe(200);
    expect((await handle(request({ issue: 8, reason: "r" }), spec)).status).toBe(200);
    expect(seen).toEqual(["governed_approve", "plan_approve"]);
  });

  it("maps a GitHub refusal and an orchestrator refusal to the standard error body", async () => {
    const spec = {
      schema,
      action: "cancel",
      loadSession: async () => session("admin"),
      act: async () => {
        const error = new Error("GitHub refused the label write (403): your token lacks write access") as Error & { status: number };
        error.status = 403;
        throw error;
      },
    };
    const response = await handle(request({ issue: 1, reason: "r" }), spec);
    expect(response.status).toBe(403);
    expect(((await response.json()) as { code: string }).code).toBe("github_refused");
    const refused = await handle(request({ issue: 1, reason: "r" }), {
      ...spec,
      act: async () => {
        throw new ApiError(409, { code: "audit_decided", message: "already decided", traceId: "t" });
      },
    });
    expect(refused.status).toBe(409);
    expect(((await refused.json()) as { code: string }).code).toBe("audit_decided");
  });
});
