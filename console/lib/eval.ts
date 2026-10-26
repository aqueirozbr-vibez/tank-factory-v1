// The Eval and versions reads and derivations (PRD Capability 5 eval set; TRD §5.2 Eval and
// versions, §6.1 `/eval/sets`, `/eval/runs`, `/config/history`): pass rate overall and per class
// by factory and configuration version, whole points against the merged baseline, the changes
// a drop over 10 points blocked and the regression that blocked them. Server only.

import { ApiError, read, type Caller } from "./api";
import type { components } from "./api.types";

export type EvalSet = components["schemas"]["EvalSetView"];
export type EvalRun = components["schemas"]["EvalRunView"];
export type HistoryItem = components["schemas"]["ConfigHistoryItem"];
export type Installation = components["schemas"]["InstallationView"];

export const BLOCK_DROP_POINTS = 10; // PRD: a pass-rate drop > 10 points blocks the change
export const GAP_POINTS = 10; // PRD: a second installation within 10 points of the pilot
export const MIN_ITEMS = 20; // PRD: an eval set holds at least 20 closed issues

/** The PRD's classes in the table's order, with the column label the UI context prints. */
export const CLASSES = [
  { key: "bug", label: "bugs" },
  { key: "test", label: "tests" },
  { key: "docs", label: "docs" },
  { key: "feature", label: "small features" },
] as const;

export type EvalData = {
  sets: EvalSet[];
  runs: EvalRun[];
  history: HistoryItem[];
  installation: Installation | null;
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

export async function loadEval(caller: Caller | null): Promise<EvalData> {
  const errors: string[] = [];
  const [sets, runs, history, installation] = await Promise.all([
    safely("eval sets", errors, () => read("/api/v1/eval/sets", { caller }), { items: [], next: null }),
    safely("eval runs", errors, () => read("/api/v1/eval/runs", { caller }), { items: [], next: null }),
    safely("config history", errors, () => read("/api/v1/config/history", { caller }), { items: [], next: null }),
    safely("installation", errors, () => read("/api/v1/installation", { caller }), null),
  ]);
  return { sets: sets.items, runs: runs.items, history: history.items, installation, errors };
}

export function points(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(Math.round(value));
}

export function signed(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(value);
  return rounded > 0 ? `+${rounded}` : rounded < 0 ? `−${Math.abs(rounded)}` : "0";
}

export function classRate(run: Pick<EvalRun, "per_class">, key: string): number | null {
  const value = run.per_class?.[key];
  return typeof value === "number" ? value : null;
}

export type Result = "blocked" | "passed" | "running" | "failed" | "pending";

/** What the run meant for the change that triggered it. */
export function resultOf(run: Pick<EvalRun, "status" | "blocked">): Result {
  if (run.status === "running") return "running";
  if (run.status === "failed" || run.status === "error") return "failed";
  if (run.blocked === true) return "blocked";
  if (run.status === "complete") return "passed";
  return "pending";
}

const TRIGGERS: Record<string, string> = {
  install_dry_run: "install dry run",
  config_pr: ".factory/ change",
  factory_upgrade: "factory upgrade",
  harness_upgrade: "harness upgrade",
  manual: "manual",
};

export function triggerText(trigger: string): string {
  return TRIGGERS[trigger] ?? trigger;
}

export function versionOf(run: Pick<EvalRun, "factory_version" | "config_sha">): string {
  return `${run.factory_version} · ${run.config_sha.slice(0, 7)}`;
}

export type VersionGroup = {
  key: string;
  factory_version: string;
  config_sha: string;
  current: boolean;
  runs: EvalRun[]; // newest first
};

/** Runs grouped by factory version and configuration, the newest group first. */
export function runsByVersion(runs: EvalRun[], history: HistoryItem[]): VersionGroup[] {
  const current = new Set(history.filter((h) => h.current).map((h) => h.config_sha));
  const groups = new Map<string, VersionGroup>();
  const sorted = [...runs].sort((a, b) => b.started_at.localeCompare(a.started_at) || b.id.localeCompare(a.id));
  for (const run of sorted) {
    const key = versionOf(run);
    const group = groups.get(key) ?? { key, factory_version: run.factory_version, config_sha: run.config_sha, current: current.has(run.config_sha), runs: [] };
    group.runs.push(run);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function runById(runs: EvalRun[]): Map<string, EvalRun> {
  return new Map(runs.map((r) => [r.id, r]));
}

export type Regression = { key: string; label: string; from: number | null; to: number | null; drop: number };

/** The per-class drops of a run against its baseline, the worst first. */
export function regressions(run: EvalRun, baseline: EvalRun | null): Regression[] {
  const out: Regression[] = [];
  for (const cls of CLASSES) {
    const from = baseline ? classRate(baseline, cls.key) : null;
    const to = classRate(run, cls.key);
    const drop = from !== null && to !== null ? from - to : 0;
    if (drop > 0) out.push({ key: cls.key, label: cls.label, from, to, drop });
  }
  return out.sort((a, b) => b.drop - a.drop);
}

export type BlockedRun = { run: EvalRun; baseline: EvalRun | null; regressions: Regression[] };

/** The changes a drop over 10 points blocked, newest first, with the regression behind each. */
export function blockedChanges(runs: EvalRun[]): BlockedRun[] {
  const byId = runById(runs);
  return runs
    .filter((r) => r.blocked === true)
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
    .map((run) => {
      const baseline = run.baseline_run_id ? (byId.get(run.baseline_run_id) ?? null) : null;
      return { run, baseline, regressions: regressions(run, baseline) };
    });
}

/** "Overall 63 → 50 (−13 pt). bugs 67 → 44." as the UI context prints an eval result line. */
export function resultLine(run: EvalRun, baseline: EvalRun | null): string {
  const overall = baseline ? `Overall ${points(baseline.pass_rate)} → ${points(run.pass_rate)} (${signed(run.delta_points)} pt).` : `Overall ${points(run.pass_rate)}.`;
  const worst = regressions(run, baseline)[0];
  return worst ? `${overall} ${worst.label} ${points(worst.from)} → ${points(worst.to)}.` : overall;
}

export function latestComplete(runs: EvalRun[], setId: string): EvalRun | null {
  const mine = runs.filter((r) => r.set_id === setId && r.status === "complete").sort((a, b) => b.started_at.localeCompare(a.started_at));
  return mine[0] ?? null;
}

export type Gap = { points: number; within: boolean } | null;

/** How far a set's latest pass rate sits below its reference (the pilot's), when one is set. */
export function gapOf(set: Pick<EvalSet, "reference_pass_rate">, latest: Pick<EvalRun, "pass_rate"> | null): Gap {
  if (set.reference_pass_rate === null || set.reference_pass_rate === undefined || !latest || latest.pass_rate === null) return null;
  const gap = set.reference_pass_rate - latest.pass_rate;
  return { points: Math.round(gap * 10) / 10, within: gap <= GAP_POINTS };
}

export function gapText(gap: Gap): string {
  if (!gap) return "no reference pass rate";
  if (gap.points <= 0) return `${Math.abs(gap.points)} points above the pilot`;
  return `${gap.points} points below the pilot${gap.within ? "" : ` (over ${GAP_POINTS})`}`;
}
