// Labels under the user's own token (TRD §6.1 BFF row `POST /api/console/labels`; PRD
// Capability 2: the GitHub label is the approval): add `factory:plan-approved` to a plan
// (`plan_approve`, or `governed_approve` when the task is risk:governed and the user owns its
// path), remove `factory:ready` (cancel) or `factory:needs-human` (resume). 409 when the spec
// sha changed since the page loaded; 429 past 30 writes a minute; GitHub's refusal passes
// through and nothing is recorded. The approvals row is the orchestrator's, written when it
// sees the label event.

import { NextResponse } from "next/server";
import { z } from "zod";

import { getJson, read, taskPath } from "@/lib/api";
import type { components } from "@/lib/api.types";
import { actionFor, codeownerOf, codeownerRules } from "@/lib/approvals";
import { loadSession, userToken } from "@/lib/auth";
import { errorResponse, handle } from "@/lib/bff";
import { addLabel, removeLabel, type Repo } from "@/lib/github";
import { LABELS_RULE, allow } from "@/lib/ratelimit";

export const dynamic = "force-dynamic";

export const schema = z
  .object({
    task_id: z.string().min(1),
    issue: z.number().int().positive(),
    add: z.enum(["factory:plan-approved"]).optional(),
    remove: z.enum(["factory:ready", "factory:needs-human"]).optional(),
    spec_sha: z.string().nullable().optional(),
  })
  .refine((body) => (body.add ? 1 : 0) + (body.remove ? 1 : 0) === 1, { message: "exactly one of add or remove" });

type Body = z.infer<typeof schema>;
type TaskDetail = components["schemas"]["TaskDetail"];

export async function POST(request: Request): Promise<Response> {
  const tasks = new Map<string, Promise<TaskDetail>>();
  const taskOf = (body: Body, login: string): Promise<TaskDetail> => {
    const key = `${login}:${body.task_id}`;
    const hit = tasks.get(key);
    if (hit) return hit;
    const fresh = getJson<TaskDetail>(taskPath(body.task_id), { caller: { actor: login } });
    tasks.set(key, fresh);
    return fresh;
  };
  const repoOf = async (login: string): Promise<Repo> => {
    const installation = await read("/api/v1/installation", { caller: { actor: login } });
    return { owner: installation.org, repo: installation.repo };
  };

  return handle(request, {
    schema,
    loadSession,
    action: async (body, session) => {
      if (body.remove === "factory:ready") return "cancel";
      if (body.remove === "factory:needs-human") return "resume";
      const task = await taskOf(body, session.login);
      return actionFor({ risk: task.task.risk });
    },
    codeowner: async (body, session, action) => {
      if (action !== "governed_approve") return false;
      const token = await userToken();
      if (!token) return false;
      const task = await taskOf(body, session.login);
      const path = task.task.governed_path ?? task.task.spec_path;
      if (!path) return false;
      const rules = await codeownerRules(token, await repoOf(session.login));
      return codeownerOf(session.login, [path], rules).get(path) ?? false;
    },
    act: async ({ body, session }) => {
      if (!allow(session.login, LABELS_RULE)) {
        return errorResponse(429, "rate_limited", `rate limit ${LABELS_RULE.name}`);
      }
      const token = await userToken();
      if (!token) return errorResponse(401, "session", "sign in to continue");
      const task = await taskOf(body, session.login);
      if (task.task.issue_number !== body.issue) {
        return errorResponse(400, "body", "the issue does not belong to the task");
      }
      const repo = await repoOf(session.login);
      if (body.add) {
        if (body.spec_sha !== undefined && body.spec_sha !== task.task.spec_sha) {
          return errorResponse(409, "spec_changed", "the spec sha changed since the page loaded; read the spec again", {
            spec_sha: task.task.spec_sha,
          });
        }
        const labels = await addLabel(token, repo, body.issue, body.add);
        return NextResponse.json({ labels, actor: session.login, bound_sha: task.task.spec_sha });
      }
      if (body.remove) await removeLabel(token, repo, body.issue, body.remove);
      return NextResponse.json({ removed: body.remove, actor: session.login });
    },
  });
}
