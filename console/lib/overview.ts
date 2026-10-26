// The Overview's reads, one place (TRD §5.2 Overview; code-standards · Next.js): KPIs,
// indicators, the needs-a-human and running/queued tables, spend by task, the installation.
// Server only; every read is live (no-store).

import { ApiError, read, type Caller } from "./api";
import type { components } from "./api.types";

export type TaskSummary = components["schemas"]["TaskSummary"];
export type MetricView = components["schemas"]["MetricView"];
export type InstallationView = components["schemas"]["InstallationView"];
export type PlanQueueItem = components["schemas"]["PlanQueueItem"];

export type OverviewData = {
  installation: InstallationView | null;
  kpis: { day: string | null; items: MetricView[] };
  indicators: { day: string | null; items: MetricView[] };
  needsHuman: { plans: PlanQueueItem[]; blocked: TaskSummary[] };
  running: TaskSummary[];
  queued: TaskSummary[];
  counts: { plans: number; questions: number; decisions: number; budget: number } | null;
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

export async function loadOverview(caller: Caller | null): Promise<OverviewData> {
  const errors: string[] = [];
  const [installation, kpis, indicators, plans, blocked, running, queued, counts] = await Promise.all([
    safely("installation", errors, () => read("/api/v1/installation", { caller }), null),
    safely("kpis", errors, () => read("/api/v1/kpis", { caller }), { day: null, window: "30d", items: [] }),
    safely("indicators", errors, () => read("/api/v1/indicators", { caller }), { day: null, items: [] }),
    safely("plans", errors, () => read("/api/v1/approvals/plans", { caller }), { items: [], next: null }),
    safely("blocked", errors, () => read("/api/v1/tasks", { caller, query: { status: "blocked" } }), {
      items: [],
      next: null,
    }),
    safely("running", errors, () => read("/api/v1/tasks", { caller, query: { status: "running" } }), {
      items: [],
      next: null,
    }),
    safely("queued", errors, () => read("/api/v1/tasks", { caller, query: { status: "queued" } }), {
      items: [],
      next: null,
    }),
    safely("counts", errors, () => read("/api/v1/needs-human/count", { caller }), null),
  ]);
  return {
    installation,
    kpis: { day: kpis.day, items: kpis.items },
    indicators: { day: indicators.day, items: indicators.items },
    needsHuman: { plans: plans.items, blocked: blocked.items },
    running: running.items,
    queued: queued.items,
    counts,
    errors,
  };
}

/** The five headline tiles in the PRD's order, each with its unit, decimals and target text. */
export const HEADLINE_TILES = [
  { metric: "factory_merge_share", label: "Factory merge share", unit: "%", decimals: 1, onTarget: (v: number) => v >= 30 },
  { metric: "merge_rate", label: "Merge rate", unit: "%", decimals: 1, onTarget: (v: number) => v >= 70 },
  { metric: "unattended_rate", label: "Unattended rate", unit: "%", decimals: 1, onTarget: (v: number) => v >= 80 },
  { metric: "audit_disagreement_low", label: "Audit disagreement", unit: "%", decimals: 1, onTarget: (v: number) => v <= 5 },
  { metric: "cost_per_merged_pr", label: "Cost per merged PR", unit: "$", decimals: 2, onTarget: null },
] as const;

export type TileStatus = "on" | "off" | "nodata";

export function tileStatus(
  metric: (typeof HEADLINE_TILES)[number],
  items: MetricView[],
): { value: number | null; status: TileStatus; note?: string } {
  const row = items.find((m) => m.metric === metric.metric);
  if (!row || row.value === null || row.value === undefined) return { value: null, status: "nodata" };
  if (metric.metric === "cost_per_merged_pr") {
    const median = items.find((m) => m.metric === "cost_per_merged_pr_median_30d")?.value ?? null;
    const over = items.find((m) => m.metric === "over_budget_count")?.value ?? 0;
    const on = median !== null && median !== undefined && row.value <= 2 * median && over === 0;
    const note = median !== null && median !== undefined ? `median $${median.toFixed(2)} · ${over} over budget` : null;
    return { value: row.value, status: on ? "on" : "off", ...(note !== null ? { note } : {}) };
  }
  const on = metric.onTarget ? metric.onTarget(row.value) : true;
  const sample =
    row.numerator !== null && row.numerator !== undefined && row.denominator !== null && row.denominator !== undefined
      ? `${row.numerator} of ${row.denominator}`
      : undefined;
  return { value: row.value, status: on ? "on" : "off", ...(sample ? { note: sample } : {}) };
}

export function targetText(metric: string): string {
  const targets: Record<string, string> = {
    factory_merge_share: "≥ 30%",
    merge_rate: "≥ 70%",
    unattended_rate: "≥ 80%",
    audit_disagreement_low: "≤ 5%",
    cost_per_merged_pr: "≤ 2× the 30-day median",
  };
  return targets[metric] ?? "";
}
