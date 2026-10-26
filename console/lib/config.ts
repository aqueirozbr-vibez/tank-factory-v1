// The Configuration screen's reads and derivations (PRD Capability 6: roster, both instruments,
// budgets and caps, protected paths, installation settings; standing configuration is never
// applied directly but proposed as a `.factory/` PR gated on the eval set; TRD §5.2 `/config`,
// §6.1 `/config`, `/config/history`, BFF `POST /api/console/config-pr`). Server only.

import { z } from "zod";

import { ApiError, read, type Caller } from "./api";
import type { components } from "./api.types";
import { openPulls, pullFiles, type PullFile, type PullRequest, type Repo } from "./github";
import type { EvalRun } from "./eval";

export type ConfigView = components["schemas"]["ConfigView"];
export type HistoryItem = components["schemas"]["ConfigHistoryItem"];
export type Installation = components["schemas"]["InstallationView"];

export const TABS = ["roster", "instruments", "budgets", "paths", "installation"] as const;
export type Tab = (typeof TABS)[number];

export function parseTab(raw: string | string[] | undefined): Tab {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return (TABS as readonly string[]).includes(value ?? "") ? (value as Tab) : "roster";
}

/** The files a change may propose: the `.factory/` set the loader reads (config/loader.py). */
export const FACTORY_FILES = [".factory/roster.yaml", ".factory/quality.yaml", ".factory/risk.yaml", ".factory/review-policy.yaml", ".factory/factory.yaml"] as const;
export type FactoryFile = (typeof FACTORY_FILES)[number];

export const BRANCH_PREFIX = "factory-config/";

export type ConfigData = {
  config: ConfigView | null;
  history: HistoryItem[];
  installation: Installation | null;
  runs: EvalRun[];
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

/** Configuration and eval reads are tagged; the `/changes` listener revalidates them. */
export async function loadConfig(caller: Caller | null): Promise<ConfigData> {
  const errors: string[] = [];
  const [config, history, installation, runs] = await Promise.all([
    safely("configuration", errors, () => read("/api/v1/config", { caller, tags: ["config"] }), null),
    safely("history", errors, () => read("/api/v1/config/history", { caller, tags: ["config", "eval"] }), { items: [], next: null }),
    safely("installation", errors, () => read("/api/v1/installation", { caller }), null),
    safely("eval runs", errors, () => read("/api/v1/eval/runs", { caller, tags: ["eval"] }), { items: [], next: null }),
  ]);
  return { config, history: history.items, installation, runs: runs.items, errors };
}

// --- the roster ------------------------------------------------------------------------------

const roleSchema = z.object({
  harness: z.string(),
  model: z.string(),
  vendor: z.string().nullable().optional(),
  thinking: z.string().nullable().optional(),
  tools: z.array(z.string()).default([]),
  writes: z.array(z.string()).default([]),
  max_turns: z.number().nullable().optional(),
  phase_cap_usd: z.number(),
  timeout_s: z.number(),
});

export type RoleRow = z.infer<typeof roleSchema> & { role: string };

export const ROLES = ["planner", "builder", "reviewer"] as const;

export function rosterRows(roster: unknown, vendors: Record<string, string> = {}): RoleRow[] {
  if (!roster || typeof roster !== "object") return [];
  const rows: RoleRow[] = [];
  for (const role of ROLES) {
    const parsed = roleSchema.safeParse((roster as Record<string, unknown>)[role]);
    if (parsed.success) rows.push({ ...parsed.data, vendor: parsed.data.vendor ?? vendors[role] ?? null, role });
  }
  return rows;
}

const commandsSchema = z.record(z.string(), z.array(z.string()));

export function qualityRows(commands: unknown): Array<{ gate: string; argv: string[] }> {
  const parsed = commandsSchema.safeParse(commands);
  if (!parsed.success) return [];
  const order = ["format", "lint", "typecheck", "test", "full_suite"];
  return order.filter((g) => parsed.data[g]).map((g) => ({ gate: g, argv: parsed.data[g] ?? [] }));
}

// --- proposed changes ------------------------------------------------------------------------

export type Edit = { find: string; replace: string };

/** Apply find/replace edits; each `find` must occur exactly once, or the edit is refused. */
export function applyEdits(text: string, edits: Edit[]): { ok: true; text: string } | { ok: false; reason: string } {
  let out = text;
  for (const [index, edit] of edits.entries()) {
    if (!edit.find) return { ok: false, reason: `edit ${index + 1}: find is empty` };
    const first = out.indexOf(edit.find);
    if (first < 0) return { ok: false, reason: `edit ${index + 1}: "${edit.find.slice(0, 60)}" is not in the file at main` };
    if (out.indexOf(edit.find, first + 1) >= 0) return { ok: false, reason: `edit ${index + 1}: "${edit.find.slice(0, 60)}" occurs more than once` };
    out = out.slice(0, first) + edit.replace + out.slice(first + edit.find.length);
  }
  if (out === text) return { ok: false, reason: "the edits change nothing" };
  return { ok: true, text: out };
}

/** `factory-config/<login>/<ts>` (TRD §6.1). */
export function branchName(login: string, now: Date = new Date()): string {
  const ts = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  return `${BRANCH_PREFIX}${login}/${ts}`;
}

export function pullTitle(file: FactoryFile): string {
  return `factory-config: ${file}`;
}

export function pullBody(input: { login: string; reason: string; file: FactoryFile }): string {
  return [
    `Reason: ${input.reason}`,
    "",
    `Opened by @${input.login} from the factory console. Not applied until merged: the orchestrator loads`,
    `\`${input.file}\` as a new configuration version from the push to main, after the eval set has run`,
    "(a pass-rate drop of more than 10 points blocks the change).",
  ].join("\n");
}

export function reasonOf(body: string | null): string | null {
  const match = /^Reason:\s*(.+)$/m.exec(body ?? "");
  return match?.[1]?.trim() ?? null;
}

export type OpenChange = { pull: PullRequest; files: PullFile[]; reason: string | null; eval: EvalRun | null };

/** The eval run the orchestrator ran for a PR: trigger `config_pr` with the PR number as its ref. */
export function evalFor(pull: Pick<PullRequest, "number">, runs: EvalRun[]): EvalRun | null {
  const mine = runs
    .filter((r) => r.trigger === "config_pr" && (/\d+/.exec(r.trigger_ref ?? "")?.[0] ?? "") === String(pull.number))
    .sort((a, b) => b.started_at.localeCompare(a.started_at));
  return mine[0] ?? null;
}

const CACHE_MS = 60_000;
const pullCache = new Map<string, { at: number; changes: Array<{ pull: PullRequest; files: PullFile[] }> }>();

/** Open `.factory/` change PRs with their diffs, under the user's token, cached 60 s per repository. */
export async function openChanges(token: string, repo: Repo, runs: EvalRun[], now = Date.now()): Promise<OpenChange[]> {
  const key = `${repo.owner}/${repo.repo}`;
  let hit = pullCache.get(key);
  if (!hit || now - hit.at >= CACHE_MS) {
    const pulls = await openPulls(token, repo, BRANCH_PREFIX);
    const changes = await Promise.all(pulls.map(async (pull) => ({ pull, files: await pullFiles(token, repo, pull.number) })));
    hit = { at: now, changes };
    pullCache.set(key, hit);
  }
  return hit.changes.map(({ pull, files }) => ({ pull, files, reason: reasonOf(pull.body), eval: evalFor(pull, runs) }));
}

export function clearOpenChangesCache(): void {
  pullCache.clear();
}

/** The PRD's protected set the installer always applies (orchestrator/constants.py PROTECTED_PATHS_DEFAULT). */
export const PRD_PROTECTED = [".factory/", ".claude/", ".github/workflows/", "tests/acceptance/", "CODEOWNERS"] as const;

export function pathKind(path: string): "prd" | "added" {
  return PRD_PROTECTED.some((p) => path === p || path.startsWith(p)) ? "prd" : "added";
}

const fileCache = new Map<string, { at: number; files: Array<{ file: FactoryFile; text: string | null }> }>();

/** The `.factory/` files as they are at main, under the user's token, cached 60 s per repository. */
export async function filesAtMain(
  token: string,
  repo: Repo,
  reader: (token: string, repo: Repo, path: string, ref: string) => Promise<string | null>,
  now = Date.now(),
): Promise<Array<{ file: FactoryFile; text: string | null }>> {
  const key = `${repo.owner}/${repo.repo}`;
  const hit = fileCache.get(key);
  if (hit && now - hit.at < CACHE_MS) return hit.files;
  const files = await Promise.all(FACTORY_FILES.map(async (file) => ({ file, text: await reader(token, repo, file, "main") })));
  fileCache.set(key, { at: now, files });
  return files;
}
