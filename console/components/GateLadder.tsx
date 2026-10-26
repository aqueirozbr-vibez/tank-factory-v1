"use client";

// The gates as run, the first failure selected, its drawer open (ui-context.md · Component
// Library · GateLadder, EvidenceDrawer): `role="list"` with position announced; after a failure
// the gates the phase would have run next read `–` skipped and are not clickable; `›` reveals
// "Open evidence"; the drawer carries the command, exit code, attempt, gate_results id and time
// and the kept output; `Esc` closes it.

import { useEffect, useMemo, useState } from "react";

import type { GateView, PhaseView } from "@/lib/task";
import { REPAIR_CAP } from "@/lib/task";

import { Chip } from "./Chip";
import styles from "./GateLadder.module.css";

export type GateLadderProps = { gates: GateView[]; phases: PhaseView[] };

/** The gates each phase runs, in order; a run stops at the first failure (orchestrator/gates). */
export const PLANNED: Record<string, readonly string[]> = {
  plan: ["envelope_schema"],
  spec_gate: ["spec_sections", "spec_ears", "spec_ac_holdout_map", "spec_adr_cited"],
  build: ["envelope_schema", "artifacts_exist", "files_non_empty", "diff_matches_claims", "permissions_enforce"],
  code_gates: ["format", "lint", "typecheck", "focused_tests"],
  review: ["envelope_schema", "verdict_consistent"],
  full_suite: ["full_suite"],
};

export type RungState = "pass" | "fail" | "skipped";

export type Rung = {
  key: string;
  name: string;
  state: RungState;
  clickable: boolean;
  gate: GateView | null; // null for a planned gate that never ran
  phaseId: string;
  attempt: number;
};

/** Rungs in run order; after a failure the rest of that phase attempt reads skipped. */
export function rungs(gates: GateView[], phases: PhaseView[]): Rung[] {
  const nameOf = new Map(phases.map((p) => [p.phase_id, p.name] as const));
  const groups = new Map<string, GateView[]>();
  for (const gate of gates) {
    const key = `${gate.phase_id}:${gate.attempt}`;
    const list = groups.get(key) ?? [];
    list.push(gate);
    groups.set(key, list);
  }
  const out: Rung[] = [];
  for (const [key, list] of groups) {
    let failedAt: GateView | null = null;
    for (const gate of list) {
      const base = { key: gate.id, name: gate.gate, gate, phaseId: gate.phase_id, attempt: gate.attempt };
      if (failedAt) out.push({ ...base, state: "skipped", clickable: false });
      else if (!gate.passed) {
        failedAt = gate;
        out.push({ ...base, state: "fail", clickable: true });
      } else out.push({ ...base, state: "pass", clickable: true });
    }
    if (failedAt) {
      const planned = PLANNED[nameOf.get(failedAt.phase_id) ?? ""] ?? [];
      const ran = new Set(list.map((g) => g.gate));
      const index = planned.indexOf(failedAt.gate);
      for (const name of planned.slice(index + 1)) {
        if (ran.has(name)) continue;
        out.push({
          key: `${key}:${name}:skipped`,
          name,
          state: "skipped",
          clickable: false,
          gate: null,
          phaseId: failedAt.phase_id,
          attempt: failedAt.attempt,
        });
      }
    }
  }
  return out;
}

/** "build 1 · repair 1 of 3" for a code_gates run; "<phase> <attempt>" otherwise. */
export function attemptLabel(rung: Pick<Rung, "phaseId" | "attempt">, phases: PhaseView[]): string {
  const phase = phases.find((p) => p.phase_id === rung.phaseId);
  if (!phase) return `attempt ${rung.attempt}`;
  if (phase.name === "code_gates") {
    const siblings = phases.filter((p) => p.run_id === phase.run_id && p.attempt === phase.attempt && p.name === "code_gates");
    const repair = siblings.findIndex((p) => p.phase_id === phase.phase_id);
    return `build ${phase.attempt} · repair ${Math.max(0, repair)} of ${REPAIR_CAP}`;
  }
  return `${phase.name} ${phase.attempt}`;
}

export function GateLadder({ gates, phases }: GateLadderProps) {
  const ladder = useMemo(() => rungs(gates, phases), [gates, phases]);
  const firstFailure = ladder.find((r) => r.state === "fail");
  const [open, setOpen] = useState<string | null>(firstFailure ? firstFailure.key : null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const selected = open !== null ? ladder.find((r) => r.key === open) : undefined;
  return (
    <section aria-labelledby="gates-title" className={styles.gates}>
      <h2 id="gates-title" className={styles.heading}>
        Gates <span className="mono text-muted">{ladder.length}</span>
      </h2>
      <div className={styles.columns}>
        {ladder.length === 0 ? (
          <p className="text-muted">No gate has run.</p>
        ) : (
          <ol role="list" className={styles.list} aria-label="Gates as run">
            {ladder.map((rung, position) => (
              <li key={rung.key} className={styles.rung} aria-posinset={position + 1} aria-setsize={ladder.length} data-row tabIndex={0}>
                <Chip kind="gate" value={rung.state} />
                <span className="mono">{rung.name}</span>
                <span className={`${styles.attempt} mono text-muted`}>{attemptLabel(rung, phases)}</span>
                {rung.clickable && rung.gate ? (
                  <button
                    type="button"
                    className={`btn btn-ghost ${styles.open}`}
                    aria-expanded={open === rung.key}
                    aria-controls={open === rung.key ? "evidence-drawer" : undefined}
                    onClick={() => setOpen(open === rung.key ? null : rung.key)}
                  >
                    › Open evidence
                  </button>
                ) : (
                  <span className={`${styles.open} text-muted`}>– skipped</span>
                )}
              </li>
            ))}
          </ol>
        )}
        {selected?.gate ? (
          <EvidenceDrawer gate={selected.gate} label={attemptLabel(selected, phases)} onClose={() => setOpen(null)} />
        ) : null}
      </div>
    </section>
  );
}

export type EvidenceDrawerProps = { gate: GateView; label: string; onClose: () => void };

export function EvidenceDrawer({ gate, label, onClose }: EvidenceDrawerProps) {
  const checks = Array.isArray(gate.checks) ? (gate.checks as Array<Record<string, unknown>>) : [];
  return (
    <aside id="evidence-drawer" className={`card blueprint ${styles.drawer}`} aria-label={`Evidence for ${gate.gate}`}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <header className={styles.drawerHead}>
        <h3 className={styles.drawerTitle}>
          {gate.gate} <Chip kind="gate" value={gate.passed ? "pass" : "fail"} />
        </h3>
        <button type="button" className="btn btn-icon" onClick={onClose} aria-label="Close evidence (Esc)">
          ×
        </button>
      </header>
      <dl className={styles.facts}>
        <dt>Attempt</dt>
        <dd className="mono">{label}</dd>
        <dt>Command</dt>
        <dd className="mono">{gate.command ?? "–"}</dd>
        <dt>Exit code</dt>
        <dd className="mono">{gate.exit_code ?? "–"}</dd>
        <dt>gate_results</dt>
        <dd className="mono">
          {gate.id} · {gate.created_at}
        </dd>
      </dl>
      {checks.length > 0 ? (
        <ul className={styles.checks}>
          {checks.map((check, index) => (
            <li key={index} className="mono">
              {check["ok"] ? "✓" : "×"} {String(check["item"] ?? "")} <span className="text-muted">{String(check["note"] ?? "")}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <pre className={`${styles.output} mono`} tabIndex={0}>{gate.output_tail ?? "(no output kept)"}</pre>
    </aside>
  );
}
