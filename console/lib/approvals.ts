// The Approvals screen's reads and derivations (PRD Capability 2 approvals and questions,
// Capability 4 monthly audit; TRD §5.2 Approvals, §6.1 `/approvals/plans`, `/approvals/questions`,
// `/audit/queue`): the three queues, the spec split into its sections, the AC→holdout table,
// the audit strata, and what the signed-in viewer may do with each plan. Server only.

import { z } from "zod";

import { ApiError, read, type Caller } from "./api";
import type { components } from "./api.types";
import { allowed } from "./authz";
import { isOwner, ownersFor, parseCodeowners, type OwnerRule } from "./codeowners";
import { codeownersText, type Repo } from "./github";

export type PlanItem = components["schemas"]["PlanQueueItem"];
export type QuestionItem = components["schemas"]["QuestionItem"];
export type AuditItem = components["schemas"]["AuditQueueItem"];

export const TABS = ["plans", "questions", "audit"] as const;
export type Tab = (typeof TABS)[number];

export function parseTab(raw: string | string[] | undefined): Tab {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return (TABS as readonly string[]).includes(value ?? "") ? (value as Tab) : "plans";
}

export function parseMonth(raw: string | string[] | undefined): string | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value && /^\d{4}-\d{2}$/.test(value) ? value : null;
}

export type ApprovalsData = {
  plans: PlanItem[];
  questions: QuestionItem[];
  audit: AuditItem[];
  month: string | null;
  errors: string[];
};

async function safely<T>(label: string, errors: string[], work: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await work();
  } catch (error) {
    errors.push(error instanceof ApiError ? `${label}: ${error.body.message}` : `${label}: unreachable`);
    return fallback;
  }
}

export async function loadApprovals(caller: Caller | null, month: string | null): Promise<ApprovalsData> {
  const errors: string[] = [];
  const [plans, questions, audit] = await Promise.all([
    safely("plans", errors, () => read("/api/v1/approvals/plans", { caller }), { items: [], next: null }),
    safely("questions", errors, () => read("/api/v1/approvals/questions", { caller }), { items: [], next: null }),
    safely("audit", errors, () => read("/api/v1/audit/queue", { caller, query: month ? { month } : {} }), { items: [], next: null }),
  ]);
  const first = audit.items[0];
  return { plans: plans.items, questions: questions.items, audit: audit.items, month: month ?? first?.month ?? null, errors };
}

// --- the spec ----------------------------------------------------------------------------------

export type Section = { title: string; body: string };

/** The spec's `## ` sections in order; text before the first heading is the preamble. */
export function parseSections(text: string | null): Section[] {
  if (!text) return [];
  const sections: Section[] = [];
  let current: Section | null = null;
  for (const line of text.split(/\r?\n/)) {
    const heading = /^##\s+(.+?)\s*$/.exec(line);
    if (heading) {
      if (current) sections.push({ ...current, body: current.body.trim() });
      current = { title: heading[1] ?? "", body: "" };
      continue;
    }
    if (!current) {
      if (line.trim() === "" || line.startsWith("# ")) continue;
      current = { title: "Preamble", body: "" };
    }
    current.body += `${line}\n`;
  }
  if (current) sections.push({ ...current, body: current.body.trim() });
  return sections;
}

const checkSchema = z.object({ item: z.string(), ok: z.boolean(), note: z.string().optional().default("") });

export type HoldoutRow = { ac: string; ok: boolean; note: string };

/** The AC→holdout existence table from the spec gate's checks (gates/spec.py). */
export function acHoldoutRows(checks: unknown[]): HoldoutRow[] {
  const rows: HoldoutRow[] = [];
  for (const raw of checks) {
    const parsed = checkSchema.safeParse(raw);
    if (parsed.success) rows.push({ ac: parsed.data.item, ok: parsed.data.ok, note: parsed.data.note });
  }
  return rows;
}

export function waitingText(since: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(since).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours} h` : `${Math.floor(hours / 24)} d`;
}

// --- what the viewer may do --------------------------------------------------------------------

export type Viewer = { login: string; permission: string } | null;

export type ApprovalAction = "plan_approve" | "governed_approve";

export type ApprovalState = {
  action: ApprovalAction;
  allowed: boolean;
  reason: string | null; // why the button is disabled, in the UI context's words
  governedPath: string | null;
};

export function actionFor(item: Pick<PlanItem, "risk">): ApprovalAction {
  return item.risk === "risk:governed" ? "governed_approve" : "plan_approve";
}

export function approvalState(item: PlanItem, viewer: Viewer, codeowner: boolean | null): ApprovalState {
  const action = actionFor(item);
  const governedPath = action === "governed_approve" ? (item.governed_path ?? item.spec_path) : null;
  if (!viewer) return { action, allowed: false, reason: "sign in to approve", governedPath };
  if (!item.spec_sha) return { action, allowed: false, reason: "the spec is not committed yet", governedPath };
  if (!allowed(viewer.permission, action, codeowner === true)) {
    const reason =
      action === "governed_approve" && allowed(viewer.permission, "plan_approve")
        ? `not a CODEOWNER of ${governedPath ?? "the governed path"}`
        : `no write access (${viewer.permission})`;
    return { action, allowed: false, reason, governedPath };
  }
  return { action, allowed: true, reason: null, governedPath };
}

// --- CODEOWNERS, cached briefly per repository (TRD §5.2: Contents reads cached 60 s) ----------

const CACHE_MS = 60_000;
const cache = new Map<string, { at: number; rules: OwnerRule[] }>();

export async function codeownerRules(token: string, repo: Repo, now = Date.now()): Promise<OwnerRule[]> {
  const key = `${repo.owner}/${repo.repo}`;
  const hit = cache.get(key);
  if (hit && now - hit.at < CACHE_MS) return hit.rules;
  const text = await codeownersText(token, repo);
  const rules = text ? parseCodeowners(text) : [];
  cache.set(key, { at: now, rules });
  return rules;
}

export function clearCodeownerCache(): void {
  cache.clear();
}

/** Whether `login` owns each governed path, from the repository's CODEOWNERS at main. */
export function codeownerOf(login: string, paths: string[], rules: OwnerRule[]): Map<string, boolean> {
  const out = new Map<string, boolean>();
  for (const path of paths) out.set(path, isOwner(login, ownersFor(path, rules)));
  return out;
}

// --- audit ---------------------------------------------------------------------------------------

export const STRATA = ["risk:low", "risk:high", "risk:governed"] as const;

export type Stratum = { stratum: string; waiting: number; rule: string };

const RULES: Record<string, string> = {
  "risk:low": "10% sampled",
  "risk:high": "every PR",
  "risk:governed": "every PR",
};

/** The queue by stratum (PRD Capability 4: low 10% random, high and governed every PR). */
export function strata(items: AuditItem[]): Stratum[] {
  return STRATA.map((stratum) => ({
    stratum,
    waiting: items.filter((i) => i.stratum === stratum).length,
    rule: RULES[stratum] ?? "",
  }));
}

/** `YYYY-MM` moved by `by` months. */
export function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number);
  const date = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1 + by, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function monthText(month: string | null): string {
  if (!month) return "this month";
  const [year, mm] = month.split("-");
  const names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  return `${names[Number(mm) - 1] ?? mm} ${year}`;
}
