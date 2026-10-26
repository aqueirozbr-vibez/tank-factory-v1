import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { behindSeconds, eventsUrl, mergeRows, type TailRow } from "@/lib/hooks/useTail";
import {
  durationMs,
  formatDuration,
  overrideOf,
  repairRounds,
  riskOf,
  riskProps,
  type EnvelopeView,
  type EventView,
  type GateView,
  type PhaseView,
  type TaskSummary,
} from "@/lib/task";

import { chainText } from "./ChainFooter";
import { EnvelopeViewer, tabsOf } from "./EnvelopeViewer";
import { GateLadder, attemptLabel, rungs } from "./GateLadder";
import { axis } from "./PhaseTimeline";
import { TraceTable, headerText, summary } from "./TraceTable";

afterEach(cleanup);

const T0 = "2026-10-24T10:00:00Z";

function phase(over: Partial<PhaseView> & Pick<PhaseView, "phase_id" | "name">): PhaseView {
  return {
    run_id: "run1",
    seq: 1,
    kind: "code",
    owner: "orchestrator",
    status: "pass",
    attempt: 1,
    started_at: T0,
    ended_at: "2026-10-24T10:01:00Z",
    cost_usd: null,
    error: null,
    harness: null,
    harness_version: null,
    model_id: null,
    phase_cap_usd: null,
    retries: 0,
    session_id: null,
    timeout_s: 60,
    tokens_cache_read: null,
    tokens_cache_write: null,
    tokens_in: null,
    tokens_out: null,
    ...over,
  };
}

function gate(id: string, name: string, passed: boolean, phaseId = "g1"): GateView {
  return {
    id,
    gate: name,
    passed,
    phase_id: phaseId,
    run_id: "run1",
    attempt: 1,
    checks: [{ item: name, ok: passed, note: "" }],
    violations: null,
    command: `uv run ${name}`,
    exit_code: passed ? 0 : 1,
    output_tail: passed ? "ok" : `${name} failed`,
    created_at: T0,
  };
}

function row(seq: number, over: Partial<TailRow> = {}): TailRow {
  return {
    seq,
    kind: "tool_call",
    actor: "builder",
    task_id: "acme/atlas-api#7",
    run_id: "run1",
    phase_id: "b1",
    parent_seq: null,
    payload: { tool: "Read", args: { path: "a.py" }, ok: true, duration_ms: 12, result_snippet: "ok" },
    tokens: null,
    started_at: `2026-10-24T10:00:${String(seq % 60).padStart(2, "0")}Z`,
    ended_at: null,
    created_at: `2026-10-24T10:00:${String(seq % 60).padStart(2, "0")}Z`,
    hash: `h${seq}`,
    ...over,
  };
}

const PHASES = [
  phase({ phase_id: "p1", name: "plan", kind: "agent", owner: "planner", seq: 1, cost_usd: 0.8 }),
  phase({ phase_id: "b1", name: "build", kind: "agent", owner: "builder", seq: 2, cost_usd: 1.9 }),
  phase({ phase_id: "g1", name: "code_gates", seq: 3, status: "fail", error: "lint" }),
  phase({ phase_id: "g2", name: "code_gates", seq: 4 }),
  phase({ phase_id: "r1", name: "review", kind: "agent", owner: "reviewer", seq: 5, status: "running", ended_at: null }),
];

const GATES = [
  gate("1", "format", true),
  gate("2", "lint", false),
  gate("3", "format", true, "g2"),
  gate("4", "lint", true, "g2"),
  gate("5", "typecheck", true, "g2"),
  gate("6", "focused_tests", true, "g2"),
];

describe("gate ladder", () => {
  it("marks the gates after a failure skipped and not clickable, from the phase's planned order", () => {
    const ladder = rungs(GATES, PHASES);
    expect(ladder.map((r) => `${r.name}:${r.state}`)).toEqual([
      "format:pass",
      "lint:fail",
      "typecheck:skipped",
      "focused_tests:skipped",
      "format:pass",
      "lint:pass",
      "typecheck:pass",
      "focused_tests:pass",
    ]);
    expect(ladder.filter((r) => r.state === "skipped").every((r) => !r.clickable && r.gate === null)).toBe(true);
    expect(attemptLabel({ phaseId: "g1", attempt: 1 }, PHASES)).toBe("build 1 · repair 0 of 3");
    expect(attemptLabel({ phaseId: "g2", attempt: 1 }, PHASES)).toBe("build 1 · repair 1 of 3");
    expect(attemptLabel({ phaseId: "p1", attempt: 1 }, PHASES)).toBe("plan 1");
  });

  it("opens the first failure's evidence, hides buttons on skipped rungs and closes on Escape", async () => {
    const { container } = render(<GateLadder gates={GATES} phases={PHASES} />);
    const items = within(screen.getByRole("list", { name: "Gates as run" })).getAllByRole("listitem");
    expect(items).toHaveLength(8);
    expect(items[2]?.querySelector("button")).toBeNull();
    expect(items[2]?.textContent).toContain("– skipped");
    const drawer = screen.getByRole("complementary", { name: "Evidence for lint" });
    expect(drawer.textContent).toContain("uv run lint");
    expect(drawer.textContent).toContain("lint failed");
    expect(drawer.textContent).toContain("build 1 · repair 0 of 3");
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "Evidence for lint" })).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "› Open evidence" })[0]!);
    expect(screen.getByRole("complementary", { name: "Evidence for format" })).toBeTruthy();
  });
});

describe("phases, repairs and risk", () => {
  it("lays phases on one axis and formats durations", () => {
    const now = new Date("2026-10-24T10:05:00Z");
    expect(axis(PHASES, now)).toEqual({ start: new Date(T0).getTime(), end: now.getTime() });
    expect(durationMs(T0, null, now)).toBe(300_000);
    expect(formatDuration(900)).toBe("900 ms");
    expect(formatDuration(42_000)).toBe("42 s");
    expect(formatDuration(125_000)).toBe("2 min 5 s");
    expect(formatDuration(3_720_000)).toBe("1 h 2 min");
  });

  it("counts repair rounds per build attempt against the cap of three", () => {
    expect(repairRounds(PHASES)).toEqual([{ attempt: 1, repairs: 1, cap: 3 }]);
    expect(repairRounds(PHASES.filter((p) => p.name === "plan"))).toEqual([]);
  });

  it("reads the risk breakdown and an override out of the events", () => {
    const risk = {
      tier: "B",
      tier_name: "service logic",
      base: 40,
      factors: [{ name: "Size", rule: "~120 lines", value: 1.3 }],
      score: 52,
      risk: "risk:high",
      equation: "40 × 1.3 = 52",
      conclusion: "52 >= 50: plan approval before build.",
      instrument_version: 1,
    };
    const log: EventView = { ...row(1), kind: "log", actor: "orchestrator", payload: { risk } };
    const control: EventView = {
      ...row(2),
      kind: "control",
      actor: "rsouza",
      payload: { action: "risk_override", old: "risk:high", new: "risk:low", reason: "reviewed by hand" },
    };
    expect(riskOf([log])?.score).toBe(52);
    expect(riskOf([{ ...log, payload: { risk: { nonsense: true } } }])).toBeNull();
    expect(overrideOf([control])).toEqual({ by: "rsouza", reason: "reviewed by hand" });
    expect(overrideOf([])).toBeNull();
    const task = { risk: "risk:low", task_id: "acme/atlas-api#7" } as TaskSummary;
    const props = riskProps(risk, task, { by: "rsouza", reason: "reviewed by hand" });
    expect(props).toMatchObject({
      instrument: "risk",
      version: 1,
      baseLabel: "tier B · service logic",
      outcome: "risk:low",
      override: { by: "rsouza", reason: "reviewed by hand" },
    });
    expect(riskProps({ ...risk, instrument_version: undefined }, task, null).version).toBe("?");
  });
});

describe("envelopes and chain", () => {
  const envelope = (over: Partial<EnvelopeView>): EnvelopeView => ({
    envelope_id: "e1",
    run_id: "run1",
    phase_id: "p1",
    agent: "planner",
    output_type: "PlanOutput",
    payload: { summary: "ok" },
    valid: true,
    violations: null,
    attempt: 1,
    created_at: T0,
    ...over,
  });

  it("builds one tab per agent, one per builder attempt, and reads the empty text", () => {
    const list = [
      envelope({}),
      envelope({ envelope_id: "e2", agent: "builder", output_type: "BuildOutput", valid: false, payload: '{"summary": 1', violations: [{ item: "files_changed", problem: "Field required" }] }),
      envelope({ envelope_id: "e3", agent: "builder", output_type: "BuildOutput", attempt: 2 }),
    ];
    expect(tabsOf(list).map((t) => t.label)).toEqual(["planner", "builder (attempt 1)", "builder (attempt 2)", "reviewer"]);
    render(<EnvelopeViewer envelopes={list} />);
    fireEvent.mouseDown(screen.getByRole("tab", { name: /builder \(attempt 1\)/ }));
    fireEvent.click(screen.getByRole("tab", { name: /builder \(attempt 1\)/ }));
    const panel = screen.getByRole("tabpanel");
    expect(panel.textContent).toContain("files_changed");
    expect(panel.textContent).toContain("Field required");
    expect(panel.textContent).toContain('{"summary": 1');
    fireEvent.mouseDown(screen.getByRole("tab", { name: "reviewer" }));
    fireEvent.click(screen.getByRole("tab", { name: "reviewer" }));
    expect(screen.getByRole("tabpanel").textContent).toContain("No envelope; phase did not return.");
  });

  it("reads the chain footer", () => {
    expect(chainText({ verified: true, records: 10002, head_hash: "abcdef0123456789", first_bad_seq: null })).toBe(
      "chain verified · 10,002 records · head abcdef012345…",
    );
    expect(chainText({ verified: false, records: 3, head_hash: null, first_bad_seq: 2 })).toBe("chain broken · first bad seq 2 · 3 records · no head");
  });
});

describe("trace table and tail", () => {
  it("merges rows newest first without duplicates and judges the lag", () => {
    const merged = mergeRows([row(5), row(4)], [row(6), row(5), row(3)], 10_000);
    expect(merged.map((r) => r.seq)).toEqual([6, 5, 4, 3]);
    expect(mergeRows([row(2), row(1)], [row(3)], 2).map((r) => r.seq)).toEqual([3, 2]);
    expect(behindSeconds("2026-10-24T10:00:00Z", new Date("2026-10-24T10:00:04Z"), true)).toBeNull();
    expect(behindSeconds("2026-10-24T10:00:00Z", new Date("2026-10-24T10:00:34Z"), true)).toBe(34);
    expect(behindSeconds("2026-10-24T10:00:00Z", new Date("2026-10-24T10:00:34Z"), false)).toBeNull();
    expect(eventsUrl("acme/atlas-api#7", { before: 100, limit: 200 })).toBe(
      "/api/console/tasks/acme%2Fatlas-api%237/events?before=100&limit=200",
    );
    expect(summary(row(1))).toMatchObject({ tool: "Read", ok: "✓", duration: "12 ms", snippet: "ok" });
    expect(
      headerText({ paused: false, transport: "sse", newestAt: "2026-10-24T10:00:00Z", rows: 10000, lastSeq: 48211, now: new Date("2026-10-24T10:00:02Z") }),
    ).toBe("live · newest row 2 s ago · 10,000 rows · cursor seq > 48211");
  });

  describe("with a stream", () => {
    type Listener = (event: { data: string }) => void;
    const sources: Array<{ url: string; listeners: Map<string, Listener>; close: () => void; onopen: (() => void) | null }> = [];

    beforeEach(() => {
      sources.length = 0;
      class FakeSource {
        url: string;
        listeners = new Map<string, Listener>();
        onopen: (() => void) | null = null;
        onerror: (() => void) | null = null;
        constructor(url: string) {
          this.url = url;
          sources.push(this);
        }
        addEventListener(name: string, listener: Listener) {
          this.listeners.set(name, listener);
        }
        close() {}
      }
      vi.stubGlobal("EventSource", FakeSource);
    });

    afterEach(() => vi.unstubAllGlobals());

    it("resumes from the last seq, tints new rows, pauses on p and keeps the rows", async () => {
      render(<TraceTable taskId="acme/atlas-api#7" initialRows={[row(3), row(2), row(1)]} initialLastSeq={3} oldestSeq={1} moreBefore={false} />);
      expect(sources[0]?.url).toBe("/api/console/tasks/acme%2Fatlas-api%237/stream?after=3");
      act(() => sources[0]?.onopen?.());
      expect(screen.getByTestId("trace-header").textContent).toContain("live");
      act(() => sources[0]?.listeners.get("row")?.({ data: JSON.stringify(row(4)) }));
      const first = screen.getByTestId("trace-table").querySelector("tbody tr");
      expect(first?.getAttribute("data-seq")).toBe("4");
      expect(first?.getAttribute("data-fresh")).toBe("true");
      expect(screen.getByTestId("trace-header").textContent).toContain("cursor seq > 4");

      fireEvent.keyDown(window, { key: "p" });
      expect(screen.getByRole("button", { name: "Resume tail" })).toBeTruthy();
      expect(screen.getByTestId("trace-header").textContent).toContain("paused");
      act(() => sources[0]?.listeners.get("row")?.({ data: JSON.stringify(row(5)) }));
      expect(screen.getByTestId("trace-table").querySelector("tbody tr")?.getAttribute("data-seq")).toBe("4");
      fireEvent.keyDown(window, { key: "p" });
      expect(screen.getByRole("button", { name: "Pause tail" })).toBeTruthy();
      expect(screen.getByTestId("trace-table").querySelector("tbody tr")?.getAttribute("data-seq")).toBe("5");
      expect(screen.getAllByRole("row")).toHaveLength(6); // header + 5
      expect(screen.getByText("start of the trace")).toBeTruthy();
    });
  });
});
