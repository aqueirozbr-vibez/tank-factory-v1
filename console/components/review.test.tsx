import { cleanup, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import {
  acRows,
  blobUrl,
  conclusionOf,
  effectOf,
  headerText,
  orderedFindings,
  parseRound,
  reviewProps,
  type ReviewView,
  type ScoredFinding,
} from "@/lib/review";

import { AcTable } from "./AcTable";
import { FindingsList } from "./FindingsList";
import { ReviewHeader } from "./ReviewHeader";

afterEach(cleanup);

function scored(over: Partial<ScoredFinding> & Pick<ScoredFinding, "requirement_id" | "severity" | "score">): ScoredFinding {
  return {
    tier: over.severity,
    location: null,
    problem: `${over.requirement_id} problem`,
    equation: `${over.base ?? 70} × 1 × 0.8 × 1 = ${over.score}`,
    dismissed_by: null,
    in_diff: true,
    notes: [],
    ...over,
  };
}

const MAJOR = scored({ requirement_id: "AC3", severity: "Major", score: 56, location: "src/billing/invoice.py:41", base: 70, spec_link: 1, evidence: 0.8, scope: 1 });
const MINOR = scored({ requirement_id: "perf", severity: "Minor", score: 6, base: 30, spec_link: 0.5, evidence: 0.4, scope: 1 });
const DISMISSED = scored({
  requirement_id: "performance",
  severity: "Major",
  score: 0,
  dismissed_by: "Caching of invoice queries.",
  notes: ["dismissed by Non-goals: Caching of invoice queries."],
  base: 70,
  spec_link: 1,
  evidence: 0.8,
  scope: 0,
});

const REVIEW: ReviewView = {
  round: 1,
  rounds: 2,
  phase_id: "r1",
  verdict: "revision_needed",
  reviewer_verdict: "accepted",
  conformance: 44,
  blocking_score: 56,
  accept_conformance: 70,
  strongest: MAJOR,
  findings: [MINOR, MAJOR],
  dismissed: [DISMISSED],
  human_reasons: [],
  missing_ac: ["AC4"],
  policy_version: 1,
  overridden_by: null,
  override_reason: null,
  envelope: {
    verdict: "accepted",
    approved: true,
    findings: [
      { requirement_id: "AC1", met: true, severity: "Nit", problem: "fine", evidence: "tests/test_invoice.py::test_due_date passes" },
      { requirement_id: "AC 3", met: false, severity: "Major", problem: "Zero terms gives the wrong date.", file: "src/billing/invoice.py", line: 41, evidence: "" },
      { requirement_id: "AC3", met: true, severity: "Nit", problem: "", evidence: "second look" },
    ],
    blocking: ["AC3"],
  },
};

describe("review derivations", () => {
  it("orders blocking, advisory, then dismissed, and judges the effect against the accept line", () => {
    expect(orderedFindings(REVIEW).map((f) => `${f.requirement_id}:${f.effect}`)).toEqual(["AC3:blocks", "perf:advisory", "performance:dismissed"]);
    expect(effectOf(scored({ requirement_id: "a", severity: "Minor", score: 30 }), 70)).toBe("advisory");
    expect(effectOf(scored({ requirement_id: "a", severity: "Minor", score: 30.1 }), 70)).toBe("blocks");
    expect(effectOf(scored({ requirement_id: "a", severity: "Minor", score: 30.1 }), 60)).toBe("advisory");
  });

  it("reads one row per acceptance criterion and names the unaddressed ones", () => {
    expect(acRows(REVIEW)).toEqual([
      { id: "AC1", met: true, severity: null, evidence: "tests/test_invoice.py::test_due_date passes", problem: "", location: null },
      { id: "AC3", met: false, severity: "Major", evidence: "second look", problem: "Zero terms gives the wrong date.", location: "src/billing/invoice.py:41" },
      { id: "AC4", met: null, severity: null, evidence: "", problem: "not addressed by the reviewer", location: null },
    ]);
    expect(acRows({ envelope: null, missing_ac: [] })).toEqual([]);
  });

  it("writes the header, the GitHub location and the round", () => {
    expect(headerText(REVIEW)).toBe("Conformance 44 · accept ≥ 70 · round 1 of 2");
    expect(headerText({ ...REVIEW, conformance: 93.5, accept_conformance: null })).toBe("Conformance 93.5 · accept ≥ 70 · round 1 of 2");
    expect(blobUrl("https://github.com/acme/atlas-api/tree/agent/7", "src/billing/invoice.py:41")).toBe(
      "https://github.com/acme/atlas-api/blob/agent/7/src/billing/invoice.py#L41",
    );
    expect(blobUrl("https://github.com/acme/atlas-api/tree/agent/7", "README.md")).toBe("https://github.com/acme/atlas-api/blob/agent/7/README.md");
    expect(blobUrl("https://github.com/acme/atlas-api/tree/agent/7", null)).toBeNull();
    expect(parseRound("2")).toBe(2);
    expect(parseRound(["3", "4"])).toBe(3);
    expect(parseRound("x")).toBeNull();
    expect(parseRound("0")).toBeNull();
    expect(parseRound(undefined)).toBeNull();
  });

  it("builds the review breakdown from the strongest finding and the policy line", () => {
    const props = reviewProps(REVIEW);
    expect(props).toMatchObject({
      instrument: "review",
      version: 1,
      base: 70,
      baseLabel: "Major (tier Major) · AC3",
      score: 56,
      outcome: "revision_needed",
      conclusion: "AC4 not addressed: revision needed",
    });
    expect(props.factors.map((f) => f.value)).toEqual([1, 0.8, 1]);
    expect(props.equation).toContain("70 × 1 × 0.8 × 1 = 56");
    expect(props.equation).toContain("conformance 100 − 56 = 44");
    expect(conclusionOf({ ...REVIEW, missing_ac: [] })).toBe("44 < 70: revision needed");
    expect(conclusionOf({ ...REVIEW, verdict: "accepted", conformance: 94, missing_ac: [] })).toBe("94 ≥ 70: accepted");
    expect(conclusionOf({ ...REVIEW, verdict: "human_decision_needed", human_reasons: ["security_blocker: AC1"] })).toBe(
      "human decision: security_blocker: AC1",
    );
    const none = reviewProps({ ...REVIEW, strongest: null, findings: [], blocking_score: 0, conformance: 100, verdict: "accepted", missing_ac: [] });
    expect(none).toMatchObject({ base: 0, baseLabel: "no active finding", equation: "100 − 0 = 100" });
    const overridden = reviewProps({ ...REVIEW, overridden_by: "rsouza", override_reason: "a Major in fact" });
    expect(overridden.override).toEqual({ by: "rsouza", reason: "a Major in fact" });
  });
});

describe("review screens", () => {
  it("lists blocking, advisory then dismissed rows and quotes the Non-goals line", async () => {
    const { container } = render(<FindingsList review={REVIEW} branchUrl="https://github.com/acme/atlas-api/tree/agent/7" />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-effect"))).toEqual(["blocks", "advisory", "dismissed"]);
    expect(screen.getByTestId("non-goals-line").textContent).toBe("Caching of invoice queries.");
    expect(screen.getByRole("link", { name: /src\/billing\/invoice.py:41/ }).getAttribute("href")).toBe(
      "https://github.com/acme/atlas-api/blob/agent/7/src/billing/invoice.py#L41",
    );
    expect(screen.getByText("Conformance 44 · accept ≥ 70 · round 1 of 2")).toBeTruthy();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("reads the empty state and the AC table", async () => {
    render(<FindingsList review={{ ...REVIEW, findings: [], dismissed: [], strongest: null }} branchUrl={null} />);
    expect(screen.getByText("No findings. Every AC met.")).toBeTruthy();
    cleanup();
    const { container } = render(<AcTable review={REVIEW} branchUrl={null} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-met"))).toEqual(["true", "false", "unaddressed"]);
    expect(screen.getByRole("img", { name: "Gate: fail, not met" })).toBeTruthy();
    expect(screen.getByText("– not addressed")).toBeTruthy();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("shows the verdict, the reviewer's own verdict and the unaddressed criteria in the header", () => {
    render(<ReviewHeader review={REVIEW} detail={null} taskId="acme/atlas-api#7" />);
    expect(screen.getByRole("img", { name: "Verdict: revision_needed" })).toBeTruthy();
    expect(screen.getByTestId("review-header").textContent).toBe("Conformance 44 · accept ≥ 70 · round 1 of 2");
    expect(screen.getByText(/reviewer said/).textContent).toContain("accepted");
    expect(screen.getByText(/Not addressed by the reviewer/).textContent).toContain("AC4");
    expect(screen.getByRole("link", { name: "acme/atlas-api#7" }).getAttribute("href")).toBe("/tasks/acme%2Fatlas-api%237");
  });
});
