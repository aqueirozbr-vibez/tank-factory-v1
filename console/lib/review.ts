// The Review findings reads and derivations (PRD Capability 4; TRD §5.2 Review findings,
// §6.1 `GET /tasks/{id}/review?round=`): the task detail for the strip and links, the round's
// scored review, and the orderings the screen shows: blocking first, then advisory, then the
// dismissed findings with the Non-goals line they were dismissed by. Server only.

import { z } from "zod";

import { ApiError, getJson, taskPath, type Caller } from "./api";
import type { components } from "./api.types";
import type { TaskDetail } from "./task";

export type ReviewView = components["schemas"]["ReviewView"];

export const ACCEPT_DEFAULT = 70; // PRD Capability 4: accept at conformance ≥ 70 unless the policy says otherwise
export const REVISION_CAP = 2; // PRD Capability 4: at most 2 revision rounds

export const scoredFindingSchema = z.object({
  requirement_id: z.string(),
  severity: z.string(),
  tier: z.string(),
  location: z.string().nullable(),
  problem: z.string(),
  equation: z.string(),
  score: z.number(),
  dismissed_by: z.string().nullable(),
  in_diff: z.boolean().nullable().optional(),
  notes: z.array(z.string()).default([]),
  base: z.number().optional(),
  spec_link: z.number().optional(),
  evidence: z.number().optional(),
  scope: z.number().optional(),
});

export type ScoredFinding = z.infer<typeof scoredFindingSchema>;

export const reviewerFindingSchema = z.object({
  requirement_id: z.string(),
  met: z.boolean(),
  severity: z.string(),
  file: z.string().nullable().optional(),
  line: z.number().nullable().optional(),
  problem: z.string(),
  impact: z.string().optional(),
  correction: z.string().optional(),
  evidence: z.string().optional(),
});

export const reviewerEnvelopeSchema = z.object({
  verdict: z.string().optional(),
  approved: z.boolean().optional(),
  findings: z.array(reviewerFindingSchema).default([]),
  blocking: z.array(z.string()).default([]),
});

export type ReviewerFinding = z.infer<typeof reviewerFindingSchema>;

export type ReviewPage = {
  detail: TaskDetail | null;
  review: ReviewView | null;
  pending: boolean;
  badRound: boolean;
  notFound: boolean;
  errors: string[];
};

/** `?round=` as a positive integer, or null for the latest round. */
export function parseRound(raw: string | string[] | undefined): number | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value || !/^\d+$/.test(value) || Number(value) < 1) return null;
  return Number(value);
}

export async function loadReview(taskId: string, round: number | null, caller: Caller | null): Promise<ReviewPage> {
  const page: ReviewPage = { detail: null, review: null, pending: false, badRound: false, notFound: false, errors: [] };
  try {
    page.detail = await getJson<TaskDetail>(taskPath(taskId), { caller });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { ...page, notFound: true };
    page.errors.push(error instanceof ApiError ? `task: ${error.body.message}` : "task: unreachable");
  }
  try {
    page.review = await getJson<ReviewView>(taskPath(taskId, "/review"), {
      caller,
      query: round !== null ? { round } : {},
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) page.pending = true;
    else if (error instanceof ApiError && error.status === 400) page.badRound = true;
    else page.errors.push(error instanceof ApiError ? `review: ${error.body.message}` : "review: unreachable");
  }
  return page;
}

export function parseFindings(items: unknown[]): ScoredFinding[] {
  const out: ScoredFinding[] = [];
  for (const item of items) {
    const parsed = scoredFindingSchema.safeParse(item);
    if (parsed.success) out.push(parsed.data);
  }
  return out;
}

export function acceptOf(review: Pick<ReviewView, "accept_conformance">): number {
  return review.accept_conformance ?? ACCEPT_DEFAULT;
}

export type Effect = "blocks" | "advisory" | "dismissed";

/** A finding blocks when its own score would put conformance under the accept line. */
export function effectOf(finding: ScoredFinding, accept: number): Effect {
  if (finding.dismissed_by) return "dismissed";
  return finding.score > 100 - accept ? "blocks" : "advisory";
}

export type OrderedFinding = ScoredFinding & { effect: Effect };

const RANK: Record<Effect, number> = { blocks: 0, advisory: 1, dismissed: 2 };

/** Blocking first, then advisory, then dismissed; each group by score, strongest first. */
export function orderedFindings(review: Pick<ReviewView, "findings" | "dismissed" | "accept_conformance">): OrderedFinding[] {
  const accept = acceptOf(review);
  const all = [...parseFindings(review.findings), ...parseFindings(review.dismissed)].map((f) => ({
    ...f,
    effect: effectOf(f, accept),
  }));
  return all.sort((a, b) => RANK[a.effect] - RANK[b.effect] || b.score - a.score || a.requirement_id.localeCompare(b.requirement_id));
}

export function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function headerText(review: Pick<ReviewView, "conformance" | "accept_conformance" | "round" | "rounds">): string {
  return `Conformance ${fmt(review.conformance)} · accept ≥ ${fmt(acceptOf(review))} · round ${review.round} of ${review.rounds}`;
}

export type AcRow = {
  id: string;
  met: boolean | null; // null: the reviewer did not address it
  severity: string | null;
  evidence: string;
  problem: string;
  location: string | null;
};

export const AC_ID = /^AC\s*(\d+)$/i;

export function normaliseAc(id: string): string | null {
  const match = AC_ID.exec(id.trim());
  return match ? `AC${match[1]}` : null;
}

/** One row per acceptance criterion the reviewer judged, plus the ones it left unaddressed. */
export function acRows(review: Pick<ReviewView, "envelope" | "missing_ac">): AcRow[] {
  const rows = new Map<string, AcRow>();
  const parsed = reviewerEnvelopeSchema.safeParse(review.envelope ?? {});
  const findings = parsed.success ? parsed.data.findings : [];
  for (const finding of findings) {
    const id = normaliseAc(finding.requirement_id);
    if (!id) continue;
    const location = finding.file ? `${finding.file}${finding.line ? `:${finding.line}` : ""}` : null;
    const existing = rows.get(id);
    if (!existing) {
      rows.set(id, {
        id,
        met: finding.met,
        severity: finding.met ? null : finding.severity,
        evidence: finding.evidence ?? "",
        problem: finding.met ? "" : finding.problem,
        location,
      });
      continue;
    }
    existing.met = existing.met && finding.met;
    if (!finding.met) {
      existing.severity = existing.severity ?? finding.severity;
      existing.problem = existing.problem ? `${existing.problem} · ${finding.problem}` : finding.problem;
      existing.location = existing.location ?? location;
    }
    if (finding.evidence) existing.evidence = existing.evidence ? `${existing.evidence} · ${finding.evidence}` : finding.evidence;
  }
  for (const missing of review.missing_ac) {
    const id = normaliseAc(missing) ?? missing;
    if (!rows.has(id)) rows.set(id, { id, met: null, severity: null, evidence: "", problem: "not addressed by the reviewer", location: null });
  }
  const number = (id: string) => Number(/\d+/.exec(id)?.[0] ?? 0);
  return [...rows.values()].sort((a, b) => number(a.id) - number(b.id) || a.id.localeCompare(b.id));
}

/** A `file:line` location on GitHub at the task's branch. */
export function blobUrl(branchUrl: string, location: string | null): string | null {
  if (!location) return null;
  const match = /^(.*?)(?::(\d+))?$/.exec(location);
  if (!match || !match[1]) return null;
  const base = branchUrl.replace("/tree/", "/blob/");
  return `${base}/${match[1]}${match[2] ? `#L${match[2]}` : ""}`;
}

const SPEC_LINK_RULES: Record<string, string> = { "1": "names an AC, Scope item, Constraint or ADR", "0.5": "names no requirement" };
const EVIDENCE_RULES: Record<string, string> = {
  "1": "file:line and a reproduction",
  "0.8": "file:line, no failing test",
  "0.4": "an assertion only",
};
const SCOPE_RULES: Record<string, string> = { "1": "inside Scope", "0": "listed in Non-goals" };

function rule(table: Record<string, string>, value: number): string {
  return table[String(value)] ?? `factor ${value}`;
}

/** The four factors of the strongest finding, from the payload or its printed equation. */
export function factorsOf(finding: ScoredFinding): { base: number; spec_link: number; evidence: number; scope: number } {
  if (finding.base !== undefined && finding.spec_link !== undefined && finding.evidence !== undefined && finding.scope !== undefined) {
    return { base: finding.base, spec_link: finding.spec_link, evidence: finding.evidence, scope: finding.scope };
  }
  const left = finding.equation.split("=")[0] ?? "";
  const parts = left.split(/\s*[×x]\s*/).map((p) => Number(p.trim()));
  const [base = 0, spec_link = 1, evidence = 1, scope = 1] = parts;
  return { base, spec_link, evidence, scope };
}

export type ReviewBreakdownProps = {
  instrument: "review";
  version: string | number;
  base: number;
  baseLabel: string;
  factors: { name: string; rule: string; value: number }[];
  score: number;
  equation: string;
  outcome: string;
  conclusion: string;
  override?: { by: string; reason: string };
};

export function conclusionOf(review: ReviewView): string {
  const accept = fmt(acceptOf(review));
  const conformance = fmt(review.conformance);
  if (review.verdict === "human_decision_needed") {
    return review.human_reasons.length ? `human decision: ${review.human_reasons.join("; ")}` : "human decision needed";
  }
  if (review.verdict === "accepted") return `${conformance} ≥ ${accept}: accepted`;
  if (review.missing_ac.length) return `${review.missing_ac.join(", ")} not addressed: revision needed`;
  return `${conformance} < ${accept}: revision needed`;
}

/** The ScoreBreakdown inputs for the review instrument: the strongest finding decides. */
export function reviewProps(review: ReviewView): ReviewBreakdownProps {
  const strongest = review.strongest ? scoredFindingSchema.safeParse(review.strongest) : null;
  const override = review.overridden_by ? { by: review.overridden_by, reason: review.override_reason ?? "" } : null;
  const common = {
    instrument: "review" as const,
    version: review.policy_version ?? "?",
    outcome: review.verdict,
    conclusion: conclusionOf(review),
    ...(override ? { override } : {}),
  };
  if (!strongest || !strongest.success) {
    return {
      ...common,
      base: 0,
      baseLabel: "no active finding",
      factors: [],
      score: review.blocking_score ?? 0,
      equation: `100 − ${fmt(review.blocking_score ?? 0)} = ${fmt(review.conformance ?? 100)}`,
    };
  }
  const f = strongest.data;
  const factors = factorsOf(f);
  return {
    ...common,
    base: factors.base,
    baseLabel: `${f.severity} (tier ${f.tier}) · ${f.requirement_id}`,
    factors: [
      { name: "Spec link", rule: rule(SPEC_LINK_RULES, factors.spec_link), value: factors.spec_link },
      { name: "Evidence", rule: rule(EVIDENCE_RULES, factors.evidence), value: factors.evidence },
      { name: "Scope", rule: rule(SCOPE_RULES, factors.scope), value: factors.scope },
    ],
    score: f.score,
    equation: `${f.equation}\nconformance 100 − ${fmt(f.score)} = ${fmt(review.conformance)}`,
  };
}
