// The BFF handler skeleton every /api/console route follows, in this order (code-standards ·
// Next.js): parse and validate the body with zod; load the session; evaluate authz.ts; act;
// return the standard error body on failure. Nothing else lives in a route handler. The action
// may depend on the body (a label write is `plan_approve` or `governed_approve` by the task's
// risk), so it can be a resolver; an orchestrator refusal passes through with its own code.

import { NextResponse } from "next/server";
import type { ZodType } from "zod";

import { ApiError } from "./api";
import { allowed } from "./authz";
import type { SessionRow } from "./sessions";

export type ErrorBody = { code: string; message: string; detail?: unknown; traceId: string };

export type Acting<T> = {
  body: T;
  session: SessionRow;
  codeowner: boolean;
  action: string;
};

export type ActionResolver<T> = (body: T, session: SessionRow) => Promise<string>;

export type HandlerSpec<T> = {
  schema: ZodType<T>;
  action: string | ActionResolver<T>;
  loadSession: () => Promise<SessionRow | null>;
  codeowner?: (body: T, session: SessionRow, action: string) => Promise<boolean>;
  act: (acting: Acting<T>) => Promise<Response>;
};

export function traceId(): string {
  return Math.random().toString(16).slice(2, 18);
}

export function errorResponse(status: number, code: string, message: string, detail?: unknown): NextResponse {
  const body: ErrorBody = { code, message, traceId: traceId() };
  if (detail !== undefined) body.detail = detail;
  return NextResponse.json(body, { status });
}

/** Why a permission does not allow an action, in the words the UI context uses. */
export function denialReason(permission: string, action: string, codeowner: boolean): string {
  if (action === "governed_approve" && !codeowner) return "this task's governed path needs a CODEOWNER";
  return `your permission (${permission}) does not allow ${action}`;
}

/** Run one BFF request through the fixed order; the user token never enters a response. */
export async function handle<T>(request: Request, spec: HandlerSpec<T>): Promise<Response> {
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return errorResponse(400, "body", "body is not JSON");
  }
  const parsed = spec.schema.safeParse(raw);
  if (!parsed.success) {
    return errorResponse(400, "body", "body does not match the request schema", parsed.error.flatten());
  }
  const session = await spec.loadSession();
  if (!session) {
    return errorResponse(401, "session", "sign in to continue");
  }
  let action: string;
  try {
    action = typeof spec.action === "function" ? await spec.action(parsed.data, session) : spec.action;
  } catch (error) {
    return failure(error);
  }
  const codeowner = spec.codeowner ? await spec.codeowner(parsed.data, session, action) : false;
  if (!allowed(session.permission, action, codeowner)) {
    return errorResponse(403, `${action}_denied`, denialReason(session.permission, action, codeowner), {
      permission: session.permission,
    });
  }
  try {
    return await spec.act({ body: parsed.data, session, codeowner, action });
  } catch (error) {
    return failure(error);
  }
}

function failure(error: unknown): NextResponse {
  if (error instanceof ApiError) {
    return errorResponse(error.status, error.body.code, error.body.message, error.body.detail);
  }
  if (error instanceof Error && "status" in error && typeof error.status === "number") {
    return errorResponse(error.status, "github_refused", error.message);
  }
  return errorResponse(502, "upstream", error instanceof Error ? error.message : "the action failed");
}
