// The Task detail's reads (TRD §3.4, §5.2 Task detail, §6.1): the detail first, because its
// chain footer carries the head seq; then, in parallel, the newest page of events (the first
// paint shows the last page), the envelopes, the gate results, the log and control events that
// hold the risk breakdown and any override, and the queue when the task is queued. Each failure
// is named; none blanks the screen. Server only.

import { z } from "zod";

import { ApiError, getJson, read, taskPath, type Caller } from "./api";
import type { components } from "./api.types";

export type TaskDetail = components["schemas"]["TaskDetail"];
export type TaskSummary = components["schemas"]["TaskSummary"];
export type EventView = components["schemas"]["EventView"];
export type EnvelopeView = components["schemas"]["EnvelopeView"];
export type GateView = components["schemas"]["GateView"];
export type PhaseView = components["schemas"]["PhaseView"];
type EventPage = components["schemas"]["EventPage"];
type EnvelopeList = components["schemas"]["EnvelopeList"];
type GateList = components["schemas"]["GateList"];

export const PAGE = 200;

export const riskSchema = z.object({
  tier: z.string(),
  tier_name: z.string().optional(),
  base: z.number(),
  factors: z.array(z.object({ name: z.string(), rule: z.string(), value: z.number() })),
  score: z.number(),
  risk: z.string().nullable(),
  equation: z.string(),
  conclusion: z.string(),
  instrument_version: z.number().nullable().optional(),
  overridden_by: z.string().nullable().optional(),
  override_reason: z.string().nullable().optional(),
});

export type RiskPayload = z.infer<typeof riskSchema>;

export type Override = { by: string; reason: string };

export type TaskPage = {
  detail: TaskDetail | null;
  events: EventView[]; // newest first
  lastSeq: number;
  oldestSeq: number | null;
  moreBefore: boolean;
  envelopes: EnvelopeView[];
  gates: GateView[];
  risk: RiskPayload | null;
  override: Override | null;
  queuedAhead: number | null;
  errors: string[];
  notFound: boolean;
};

async function safely<T>(label: string, errors: string[], work: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await work();
  } catch (error) {
    errors.push(error instanceof ApiError ? `${label}: ${error.body.message}` : `${label}: unreachable`);
    return fallback;
  }
}

/** The newest page: the rows before the head, newest first (TRD §3.4). */
export async function newestEvents(taskId: string, caller: Caller | null, headSeq: number): Promise<EventPage> {
  if (headSeq <= 0) return { items: [], next: null };
  return getJson<EventPage>(taskPath(taskId, "/events"), { caller, query: { before: headSeq + 1, limit: PAGE } });
}

/** The risk breakdown the plan phase logged, the latest one when the spec was corrected. */
export function riskOf(logs: EventView[]): RiskPayload | null {
  for (const event of [...logs].reverse()) {
    const payload = event.payload;
    if (payload && typeof payload === "object" && "risk" in payload) {
      const parsed = riskSchema.safeParse((payload as { risk: unknown }).risk);
      if (parsed.success) return parsed.data;
    }
  }
  return null;
}

/** The latest risk override control, by login and reason (PRD Capability 2). */
export function overrideOf(controls: EventView[]): Override | null {
  for (const event of [...controls].reverse()) {
    const payload = event.payload as Record<string, unknown> | null;
    if (payload && payload["action"] === "risk_override") {
      return { by: event.actor, reason: typeof payload["reason"] === "string" ? payload["reason"] : "" };
    }
  }
  return null;
}

export async function loadTask(taskId: string, caller: Caller | null): Promise<TaskPage> {
  const errors: string[] = [];
  const empty: TaskPage = {
    detail: null,
    events: [],
    lastSeq: 0,
    oldestSeq: null,
    moreBefore: false,
    envelopes: [],
    gates: [],
    risk: null,
    override: null,
    queuedAhead: null,
    errors,
    notFound: false,
  };
  let notFound = false;
  const detail = await safely<TaskDetail | null>(
    "task",
    errors,
    async () => {
      try {
        return await getJson<TaskDetail>(taskPath(taskId), { caller });
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          notFound = true;
          return null;
        }
        throw error;
      }
    },
    null,
  );
  if (detail === null) return { ...empty, notFound };
  const headSeq = detail.chain.head_seq ?? 0;
  const none: EventPage = { items: [], next: null };
  const [page, envelopes, gates, logs, controls, queued] = await Promise.all([
    safely("events", errors, () => newestEvents(taskId, caller, headSeq), none),
    safely("envelopes", errors, () => getJson<EnvelopeList>(taskPath(taskId, "/envelopes"), { caller }), { items: [], next: null }),
    safely("gates", errors, () => getJson<GateList>(taskPath(taskId, "/gates"), { caller }), { items: [], next: null }),
    safely("risk", errors, () => getJson<EventPage>(taskPath(taskId, "/events"), { caller, query: { kind: "log", limit: PAGE } }), none),
    safely("controls", errors, () => getJson<EventPage>(taskPath(taskId, "/events"), { caller, query: { kind: "control", limit: PAGE } }), none),
    detail.task.status === "queued"
      ? safely("queue", errors, () => read("/api/v1/tasks", { caller, query: { status: "queued" } }), { items: [], next: null })
      : Promise.resolve(null),
  ]);
  const seqs = page.items.map((e) => e.seq);
  const position = queued ? queued.items.findIndex((t) => t.task_id === taskId) : -1;
  return {
    detail,
    events: page.items,
    lastSeq: seqs.length > 0 ? Math.max(...seqs) : headSeq,
    oldestSeq: seqs.length > 0 ? Math.min(...seqs) : null,
    moreBefore: page.next !== null && page.next !== undefined,
    envelopes: envelopes.items,
    gates: gates.items,
    risk: riskOf(logs.items),
    override: overrideOf(controls.items),
    queuedAhead: position >= 0 ? position : null,
    errors,
    notFound: false,
  };
}

/** Phase rows grouped by attempt, in order, for the timeline and the repair rounds. */
export function phasesByAttempt(phases: PhaseView[]): Map<number, PhaseView[]> {
  const groups = new Map<number, PhaseView[]>();
  for (const phase of phases) {
    const list = groups.get(phase.attempt) ?? [];
    list.push(phase);
    groups.set(phase.attempt, list);
  }
  return groups;
}

export function durationMs(startedAt: string, endedAt: string | null, now: Date = new Date()): number {
  const end = endedAt ? new Date(endedAt).getTime() : now.getTime();
  return Math.max(0, end - new Date(startedAt).getTime());
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export const REPAIR_CAP = 3; // PRD Capability 3: at most 3 repair rounds in the same builder session

/** Repair rounds are the failed code_gates phases of a build attempt. */
export function repairRounds(phases: PhaseView[]): { attempt: number; repairs: number; cap: number }[] {
  const rounds: { attempt: number; repairs: number; cap: number }[] = [];
  for (const [attempt, list] of phasesByAttempt(phases)) {
    if (!list.some((p) => p.name === "build")) continue;
    const repairs = list.filter((p) => p.name === "code_gates" && p.status === "fail").length;
    rounds.push({ attempt, repairs, cap: REPAIR_CAP });
  }
  return rounds;
}

export type RiskBreakdownProps = {
  instrument: "risk";
  version: string | number;
  base: number;
  baseLabel: string;
  factors: { name: string; rule: string; value: number }[];
  score: number;
  equation: string;
  outcome: string;
  conclusion: string;
  tier: string;
  override?: Override;
};

/** The ScoreBreakdown inputs for the risk instrument, from the logged breakdown and the task row. */
export function riskProps(risk: RiskPayload, task: Pick<TaskSummary, "risk">, override: Override | null): RiskBreakdownProps {
  const fromPayload = risk.overridden_by && risk.override_reason ? { by: risk.overridden_by, reason: risk.override_reason } : null;
  const shown = override ?? fromPayload;
  return {
    instrument: "risk",
    version: risk.instrument_version ?? "?",
    base: risk.base,
    baseLabel: risk.tier_name ? `tier ${risk.tier} · ${risk.tier_name}` : `tier ${risk.tier}`,
    factors: risk.factors,
    score: risk.score,
    equation: risk.equation,
    outcome: task.risk ?? risk.risk ?? "unclassified",
    conclusion: risk.conclusion,
    tier: risk.tier,
    ...(shown ? { override: shown } : {}),
  };
}
