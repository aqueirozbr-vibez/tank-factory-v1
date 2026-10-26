// A `.factory/` change as a pull request under the user's token (TRD §6.1 BFF row
// `POST /api/console/config-pr`: `{file, edits[], reason}` → `{pr_url, branch}`; branch
// `factory-config/<login>/<ts>`; 10 a day per login; 422 when the branch exists; PRD Capability
// 6: nothing is applied before merge, the eval set gates the change). BFF order: body, session,
// authz (`config_pr`), act.

import { NextResponse } from "next/server";
import { z } from "zod";

import { read } from "@/lib/api";
import { loadSession, userToken } from "@/lib/auth";
import { errorResponse, handle } from "@/lib/bff";
import { FACTORY_FILES, applyEdits, branchName, clearOpenChangesCache, pullBody, pullTitle } from "@/lib/config";
import { GitHubRefused, contentsAt, createBranch, openPullRequest, putContents, refSha, type Repo } from "@/lib/github";
import { CONFIG_PR_RULE, allow } from "@/lib/ratelimit";

export const dynamic = "force-dynamic";

export const schema = z.object({
  file: z.enum(FACTORY_FILES),
  edits: z.array(z.object({ find: z.string().min(1), replace: z.string() })).min(1).max(20),
  reason: z.string().min(1).max(500),
});

export async function POST(request: Request): Promise<Response> {
  return handle(request, {
    schema,
    action: "config_pr",
    loadSession,
    act: async ({ body, session }) => {
      if (!allow(session.login, CONFIG_PR_RULE)) {
        return errorResponse(429, "rate_limited", `rate limit ${CONFIG_PR_RULE.name}`);
      }
      const token = await userToken();
      if (!token) return errorResponse(401, "session", "sign in to continue");
      const installation = await read("/api/v1/installation", { caller: { actor: session.login } });
      const repo: Repo = { owner: installation.org, repo: installation.repo };
      const current = await contentsAt(token, repo, body.file, "main");
      if (!current) return errorResponse(422, "file_missing", `${body.file} does not exist at main`);
      const applied = applyEdits(current.text, body.edits);
      if (!applied.ok) return errorResponse(422, "edit_rejected", applied.reason);
      const branch = branchName(session.login);
      const mainSha = await refSha(token, repo, "main");
      try {
        await createBranch(token, repo, branch, mainSha);
      } catch (error) {
        if (error instanceof GitHubRefused && error.status === 422) {
          return errorResponse(422, "branch_exists", `branch ${branch} already exists; try again`);
        }
        throw error;
      }
      await putContents(token, repo, body.file, {
        branch,
        message: `${pullTitle(body.file)}\n\n${body.reason}`,
        text: applied.text,
        sha: current.sha,
      });
      const pull = await openPullRequest(token, repo, {
        title: pullTitle(body.file),
        body: pullBody({ login: session.login, reason: body.reason, file: body.file }),
        head: branch,
        base: "main",
      });
      clearOpenChangesCache();
      return NextResponse.json({ pr_url: pull.html_url, branch, number: pull.number }, { status: 201 });
    },
  });
}
